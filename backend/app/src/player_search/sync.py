"""Keeps the in-memory player search index in sync with MongoDB.

Guarantees:
- A player added or removed through the API is found, or gone, as soon as
  upsert or remove returns. The routes call them right after their Mongo
  write, before responding.
- Renames and deactivations by the data scraper, which never calls the API,
  show up within one refresh interval. A refresh reads only the players
  whose searchChangedAt moved since the previous read, so it stays cheap at
  any number of players. A rare full read repairs anything a writer missed.
  It reads page by page with pauses in between, so even a million players
  never hold the event loop for more than a few milliseconds at a time.
- A refresh never undoes an API change made while its Mongo read was
  loading. Every API change is logged with a generation number, and a read
  only overrides players nobody changed since it started.
- A restart loads the last saved index from disk instead of building one,
  then reads the changes since it was saved. A missing, outdated or damaged
  snapshot falls back to a full build. The index is saved at most once per
  snapshot interval and on shutdown: each save copies the whole index on the
  event loop, about half a second at a million players.
- The API starts even if the first build fails. Search answers 503 until a
  background retry succeeds, and adds made in the meantime are replayed onto
  the new index.

NOTE Only valid with a single API process. A second uvicorn worker or API
replica would hold its own index and miss the other one's adds until its
next refresh. Publish adds and removes over Redis before scaling out.

TODO Past a few million players, one process outgrows its RAM (~320 bytes
per player) and build time. Two ways out, combinable:
- Shard by crc32(tag key) % k. Tags are base-14 account numbers, so their
  first character is skewed (in 375k battle tags "2" leads 18.8%, "0"
  never), and alphabetical shards drift to 0.7-1.5x of even at 8 shards;
  the hash stays within 1%. Every name search still asks all shards and
  merges their top `limit`, which the total order keeps exact.
- Move the lists to disk: one B-tree row per list entry, keyed by (piece,
  sort key), in SQLite WITHOUT ROWID or LMDB. Short queries and the early
  stopping walk become ordered range reads, updates O(log n) row changes,
  and no build or snapshot is needed. ~1 KB per player on disk, served
  from the OS page cache.
"""

import asyncio
import time
from contextlib import suppress
from datetime import datetime, timedelta
from typing import NamedTuple

from mongo import (
    MongoConn,
    ensure_search_change_index,
    get_players_changed_since,
    get_server_time,
    get_tracked_players,
    get_tracked_players_page,
)

from .index import PlayerSearchIndex, SearchResult
from .normalize import normalize_tag
from .snapshot import SNAPSHOT_PATH, load_snapshot, save_snapshot

# Diffs hand the event loop back after this much work, so a big batch of
# scraper renames cannot stall requests. A time budget, not a count: an
# unchanged player costs about a microsecond, an upsert about half a
# millisecond, so no fixed count suits both.
_DIFF_SLICE_S = 0.01  # 10 milliseconds

# Change reads start this long before the previous read. $$NOW is taken
# when a write starts, not when it commits, so a write still in flight
# during the previous read carries a time from before it. Re-reading a few
# players is free: unchanged ones are skipped.
_CHANGE_OVERLAP = timedelta(minutes=1)

# A full refresh reads this many players per page, then pauses. A page is
# one short indexed Mongo read and a few milliseconds of decoding; reading
# all players in one cursor decodes batches of up to 16 MB at once, which
# held the event loop for ~0.9 s at a million players.
_FULL_PAGE_SIZE = 5000
# The pause after each page, so a full refresh shares the process with
# requests instead of running flat out. Adds ~4 s at a million players.
_FULL_PAGE_PAUSE_S = 0.02  # 20 milliseconds

# A snapshot with more changes since its save than this share of its
# players is rebuilt instead: an upsert costs about as much as building 15
# players, so past roughly a tenth, catching up is slower than a build.
_REBUILD_SHARE = 10


class _TimeSlice:
    """Hands the event loop back once a slice of work time is spent."""

    def __init__(self, length_s: float):
        self._length_s = length_s
        self._ends = time.perf_counter() + length_s

    async def yield_if_spent(self) -> None:
        if time.perf_counter() >= self._ends:
            await asyncio.sleep(0)
            # Measured after the sleep: time spent on other tasks is not
            # this loop's work.
            self._ends = time.perf_counter() + self._length_s


class SearchIndexNotReady(Exception):
    """The first index build has not succeeded yet."""


class _Change(NamedTuple):
    generation: int
    tag: str
    name: str | None
    removed: bool


class PlayerSearchService:
    """Owns the search index, its startup load, its refreshes and snapshots."""

    def __init__(
        self,
        mongo: MongoConn,
        refresh_interval_s: float,
        full_refresh_interval_s: float,
        retry_s: float,
        snapshot_path: str | None = SNAPSHOT_PATH,
        snapshot_interval_s: float = 30 * 60,
    ):
        self._mongo = mongo
        self._refresh_interval_s = refresh_interval_s
        self._full_refresh_interval_s = full_refresh_interval_s
        self._retry_s = retry_s
        # None or empty disables snapshots, so every start builds anew.
        self._snapshot_path = snapshot_path or None
        self._snapshot_interval_s = snapshot_interval_s
        # time.monotonic() before which a refresh does not save again.
        self._next_save_at = 0.0
        self._index: PlayerSearchIndex | None = None
        self._generation = 0
        # tag key -> latest API change. Kept until a read that started after
        # the change has been applied, which then already contains it.
        self._changes: dict[str, _Change] = {}
        # Mongo server time at the start of the last applied read. The index
        # holds every change from before it; the next read starts there.
        self._read_at: datetime | None = None
        # time.monotonic() at which the next refresh reads every player.
        self._full_due_at = 0.0
        # The index differs from the saved snapshot. Only then is it written:
        # a read time that moved without any change is not worth a write, the
        # next start just reads a little further back.
        self._unsaved = False
        self._task: asyncio.Task | None = None

    @property
    def ready(self) -> bool:
        return self._index is not None

    async def start(self) -> None:
        """Load or build the index once, then keep refreshing it.

        A failed first build is logged, not raised: the rest of the API does
        not depend on search.
        """

        try:
            await ensure_search_change_index(self._mongo)
        except Exception as e:
            # Change reads still work without it, by scanning the players.
            print(f"[WARNING] [SEARCH] Could not create the change index: {e}")
        try:
            await self.refresh()
        except Exception as e:
            print(f"[WARNING] [SEARCH] Initial index build failed, retrying: {e}")
        self._task = asyncio.create_task(self._refresh_loop())

    async def close(self) -> None:
        """Stop refreshing and save the index, so the next start loads it."""

        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        await self._save(force=True)

    def search(self, query: str, limit: int) -> list[SearchResult]:
        """Search the index. See PlayerSearchIndex.search.

        Raises:
            SearchIndexNotReady: If no build has succeeded yet.
        """

        if self._index is None:
            raise SearchIndexNotReady()
        return self._index.search(query, limit)

    def upsert(self, tag: str, name: str | None) -> None:
        """Index a player the API just stored as tracked. Never raises.

        HAS to be called after the Mongo write: a refresh whose read started
        before this call trusts the logged change over the read.
        """

        self._log_change(tag, name, removed=False)
        if self._index is None:
            return
        try:
            self._index.upsert(tag, name)
        except Exception as e:
            # The player is stored; the next refresh indexes them.
            print(f"[ERROR] [SEARCH] Could not index {tag}: {e}")

    def remove(self, tag: str) -> None:
        """Drop a player the API just deactivated. Never raises.

        HAS to be called after the Mongo write, like upsert.
        """

        self._log_change(tag, None, removed=True)
        if self._index is None:
            return
        try:
            self._index.remove(tag)
        except Exception as e:
            print(f"[ERROR] [SEARCH] Could not remove {tag} from the index: {e}")

    def _log_change(self, tag: str, name: str | None, removed: bool) -> None:
        self._generation += 1
        self._unsaved = True
        self._changes[normalize_tag(tag)] = _Change(
            self._generation, tag, name, removed
        )

    def _changed_since(self, tag: str, generation: int) -> bool:
        change = self._changes.get(normalize_tag(tag))
        return change is not None and change.generation > generation

    async def refresh(self) -> None:
        """Bring the index up to date with the tracked players in Mongo.

        The first call loads the saved snapshot or builds the index in a
        worker thread. Later calls read only the changed players, and every
        full refresh interval all of them, page by page. Both apply on the
        event loop in time slices. A changed index is saved once the snapshot
        interval since the last save has passed.

        Raises:
            Exception: If a Mongo read or the build fails. The current
                index stays in place.
        """

        # API changes logged after this point may be missing from the read
        # below, so they win over it.
        started = self._generation
        # Taken BEFORE the read: a change committed during the read may or
        # may not be in it, and the next read covers it either way.
        read_at = await get_server_time(self._mongo)

        if self._index is None:
            await self._load_or_build(started)
        elif time.monotonic() >= self._full_due_at:
            await self._apply_full(started)
            self._full_due_at = time.monotonic() + self._full_refresh_interval_s
        else:
            changes = await get_players_changed_since(
                self._mongo, self._read_at - _CHANGE_OVERLAP
            )
            await self._apply_changes(self._index, changes, started)
        self._read_at = read_at

        # Changes up to `started` were written to Mongo before the read, so
        # it already holds them and later ones still matter.
        self._changes = {
            key: change
            for key, change in self._changes.items()
            if change.generation > started
        }
        await self._save()

    async def _load_or_build(self, started: int) -> None:
        began = time.perf_counter()
        loaded = await self._load(started)
        if loaded is not None:
            index, source = loaded
        else:
            players = await get_tracked_players(self._mongo)
            index = await asyncio.to_thread(PlayerSearchIndex.build, players.items())
            source = "a full build"
            self._unsaved = True
            # Built from every player, so the next full read is a full
            # interval away. A loaded snapshot keeps the due time of 0: its
            # first refresh reads every player once, which repairs anything a
            # writer changed without moving searchChangedAt.
            self._full_due_at = time.monotonic() + self._full_refresh_interval_s

        # Adds and removes made while loading are missing from it.
        # _Change sorts by generation first, so they replay in order.
        for change in sorted(self._changes.values()):
            if change.generation > started:
                if change.removed:
                    index.remove(change.tag)
                else:
                    index.upsert(change.tag, change.name)
        self._index = index
        stats = index.stats()
        print(
            f"[INFO] [SEARCH] Indexed {stats['players']} players "
            f"({stats['listEntries']} list entries) from {source} in "
            f"{time.perf_counter() - began:.1f} s"
        )

    async def _load(self, started: int) -> tuple[PlayerSearchIndex, str] | None:
        """Load the saved snapshot and catch it up with Mongo.

        Returns:
            tuple[PlayerSearchIndex, str] | None: The index and a description
                for the log, None when there is no usable snapshot or a build
                is faster than catching up.
        """

        if self._snapshot_path is None:
            return None
        snapshot = await asyncio.to_thread(load_snapshot, self._snapshot_path)
        if snapshot is None:
            return None
        changes = await get_players_changed_since(
            self._mongo, snapshot.read_at - _CHANGE_OVERLAP
        )
        if len(changes) > len(snapshot.index) // _REBUILD_SHARE:
            print(
                f"[INFO] [SEARCH] {len(changes)} players changed since the index "
                "snapshot, building anew"
            )
            return None
        await self._apply_changes(snapshot.index, changes, started)
        return snapshot.index, f"a snapshot of {snapshot.read_at:%Y-%m-%d %H:%M} UTC"

    async def _apply_changes(
        self,
        index: PlayerSearchIndex,
        changes: dict[str, tuple[str | None, bool]],
        started: int,
    ) -> None:
        updated = removed = 0
        time_slice = _TimeSlice(_DIFF_SLICE_S)
        for tag, (name, active) in changes.items():
            await time_slice.yield_if_spent()
            # Checked right before applying: the loop may have run API
            # changes during the sleep.
            if self._changed_since(tag, started):
                continue
            # The index stores a missing name as "".
            if active and index.get(tag) != (name or ""):
                index.upsert(tag, name)
                updated += 1
            elif not active and index.remove(tag):
                removed += 1
        self._log_applied(updated, removed)

    async def _apply_full(self, started: int) -> None:
        index = self._index
        # seen[slot] marks a player found active in Mongo. One byte per slot
        # instead of a set of a million tag strings (~100 MB at that size).
        seen = bytearray(index.slot_count())
        updated = removed = 0
        time_slice = _TimeSlice(_DIFF_SLICE_S)
        after = None
        while True:
            page = await get_tracked_players_page(self._mongo, after, _FULL_PAGE_SIZE)
            for tag, name in page:
                await time_slice.yield_if_spent()
                # Checked right before applying: the loop may have run API
                # changes during a sleep. The index stores a missing name as "".
                if index.get(tag) != (name or "") and not self._changed_since(
                    tag, started
                ):
                    index.upsert(tag, name)
                    updated += 1
                slot = index.slot_of(tag)
                # A player new to the index got a slot past the old count;
                # it is in Mongo, so it is not stale anyway.
                if slot is not None and slot < len(seen):
                    seen[slot] = 1
            if len(page) < _FULL_PAGE_SIZE:
                break
            after = page[-1][0]
            await asyncio.sleep(_FULL_PAGE_PAUSE_S)

        # Indexed but not active in Mongo: deactivated by the scraper, or
        # changed by the API during the read (guarded by _changed_since).
        # Pages are not one consistent read; a player deactivated after its
        # page was read stays until the next change read, which covers
        # everything since this refresh started. A slot freed and reused by
        # the API during the read belongs to an API change, also guarded.
        for slot, was_seen in enumerate(seen):
            await time_slice.yield_if_spent()
            if was_seen:
                continue
            tag = index.tag_at(slot)
            if (
                tag is not None
                and not self._changed_since(tag, started)
                and index.remove(tag)
            ):
                removed += 1
        self._log_applied(updated, removed)

    def _log_applied(self, updated: int, removed: int) -> None:
        if updated or removed:
            self._unsaved = True
            print(
                f"[INFO] [SEARCH] Refresh applied {updated} new or renamed and "
                f"{removed} removed players"
            )

    async def _save(self, force: bool = False) -> None:
        """Save a changed index for the next start. Never raises.

        Args:
            force (bool): Save now, even within the snapshot interval of the
                last save. For shutdown.
        """

        if self._snapshot_path is None or self._index is None or not self._unsaved:
            return
        # A restart catches up on what a skipped save misses, which costs far
        # less than copying the whole index after every changed minute.
        if not force and time.monotonic() < self._next_save_at:
            return
        self._next_save_at = time.monotonic() + self._snapshot_interval_s
        # Copied on the event loop, so it cannot change while the worker
        # thread writes it. Marked saved BEFORE the write: a change made
        # during it is not in the copy and has to mark the index unsaved again.
        state = self._index.to_state()
        self._unsaved = False
        try:
            await asyncio.to_thread(
                save_snapshot, self._snapshot_path, state, self._read_at
            )
        except Exception as e:
            self._unsaved = True
            print(f"[WARNING] [SEARCH] Could not save the index snapshot: {e}")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(
                self._refresh_interval_s if self.ready else self._retry_s
            )
            try:
                await self.refresh()
            except Exception as e:
                print(f"[WARNING] [SEARCH] Index refresh failed: {e}")
