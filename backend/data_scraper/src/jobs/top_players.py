"""Tracks the top players of the last finished ranked season, once per season.

The meta analysis needs the best players' battles, whether or not anyone
opens their pages. Path of Legend is Clash Royale's ranked mode (the ladder
past the trophy road, reset every season), so its global leaderboard is
where the best players are. The top TOP_PLAYERS_N of it for the last
finished season get topFinish: they are never parked
and cannot be removed by users until the next season's list replaces them.
Players who were tracked for their own sake (regular) stay tracked after
that; the others are parked, keeping their page and their place in the
search.

TOP_PLAYERS_N slots of the scraper's capacity are reserved by count. The
other players share max_players - TOP_PLAYERS_N, so a complete new list
always fits once the rollover parked the least viewed others down to that
budget, in the same pass. The new players are tracked first, so the
reconciler can schedule them before that parking ends: a pass of minutes
over capacity, which the spread first syncs and the backlog spreading
absorb.

The rollover is idempotent and resumable. The fetched list is stored in
top_seasons before anything is applied, and appliedTopN is only set at the
end, so a crash halfway resumes from the same list on the next pass. A
changed TOP_PLAYERS_N makes the next pass fetch and apply the list again, so
the protected players match the reserved slots after a deploy. Mongo is
written before the schedules, as the reconciler expects. The new list is
protected before the old one loses its protection, so there is no moment in
which neither is.
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

import httpx

from api_key_store import NoKeyAvailable
from clash_royale_api import ClashRoyaleAPI
from intervals import jitter
from jobs.parking import park_players, parking_lock
from mongo import (
    MongoConn,
    get_former_top_players,
    get_parkable_players_count,
    get_top_players_awaiting_sync,
    get_top_season,
    mark_top_season_applied,
    migrate_regular_players,
    retire_former_top_player,
    save_top_season,
    write_top_finish,
)
from scrape_schedule import (
    Capacity,
    Schedule,
    budget_degraded,
    parking_budget,
    read_capacity,
    schedule_settings,
)
from seasons import (
    last_finished_season_id,
    next_season_start,
    season_bounds,
    season_id_at,
)
from settings import settings

logger = logging.getLogger(__name__)


class LeaderboardNotPublished(Exception):
    """The Clash Royale API has no leaderboard for the season (yet)."""


class InvalidLeaderboard(Exception):
    """A leaderboard page that cannot be part of a complete list.

    Raised instead of keeping what was read so far: a stored list is applied
    and never fetched again, so a truncated one would stay for a season.
    """


async def fetch_leaderboard(cr_api: ClashRoyaleAPI, season: str) -> list[dict]:
    """Read the top TOP_PLAYERS_N of a season's leaderboard, page by page.

    Args:
        cr_api (ClashRoyaleAPI): Client of the scraper's key pool.
        season (str): Season id as "YYYY-MM".

    Returns:
        list[dict]: {"tag", "name", "rank"} per player, best first. Shorter
        than TOP_PLAYERS_N only if the leaderboard ends before, i.e. its last
        page has no further cursor.

    Raises:
        LeaderboardNotPublished: If the API answers 404 for the season.
        InvalidLeaderboard: If a page is malformed, empty while a cursor
            follows it, or the cursors stop advancing.
        Exception: Any other Clash Royale API error.
    """

    limit = schedule_settings.TOP_PLAYERS_N
    # Twice the pages a complete list needs. Pages of skipped entries (bad or
    # repeated tags, ranks that do not advance) still advance, so a
    # leaderboard that keeps handing out new cursors without new players ends
    # here instead of never.
    max_pages = 2 * -(-limit // settings.TOP_RANKINGS_PAGE_SIZE)
    players: list[dict] = []
    seen: set[str] = set()
    cursors: set[str] = set()
    after = None
    for _ in range(max_pages):
        page = await _fetch_page(
            cr_api,
            season,
            min(settings.TOP_RANKINGS_PAGE_SIZE, limit - len(players)),
            after,
        )
        items, after = _page_parts(page, season)
        if not items and after is not None:
            raise InvalidLeaderboard(f"empty page with a cursor in {season}")
        last_rank = players[-1]["rank"] if players else 0
        players += _new_entries(cr_api, season, items, seen, last_rank)
        if after is None or len(players) >= limit:
            return players[:limit]
        if after in cursors:
            raise InvalidLeaderboard(f"repeated cursor in {season}")
        cursors.add(after)
    raise InvalidLeaderboard(f"no complete list of {season} after {max_pages} pages")


def _page_parts(page, season: str) -> tuple[list, str | None]:
    """The entries of a page and the cursor of the next one, None on the last.

    Raises:
        InvalidLeaderboard: If the page does not have the documented shape.
    """

    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise InvalidLeaderboard(f"page without an items list in {season}")
    # Only an absent paging, cursors or after marks the last page. Any other
    # shape would end the list early, and a truncated list stays a season.
    paging = page.get("paging")
    if paging is None:
        return page["items"], None
    if not isinstance(paging, dict):
        raise InvalidLeaderboard(f"unusable paging {paging!r} in {season}")
    cursors = paging.get("cursors")
    if cursors is None:
        return page["items"], None
    if not isinstance(cursors, dict):
        raise InvalidLeaderboard(f"unusable cursors {cursors!r} in {season}")
    after = cursors.get("after")
    if after is not None and (not isinstance(after, str) or not after):
        raise InvalidLeaderboard(f"unusable cursor {after!r} in {season}")
    return page["items"], after


async def _fetch_page(
    cr_api: ClashRoyaleAPI, season: str, limit: int, after: str | None
) -> dict:
    """One leaderboard page, waiting for a key while the workers hold them all.

    The workers keep every key busy, most of all while they work off the
    backlog after a start, which is exactly when the first rollover runs.
    Waiting per page keeps the pages fetched so far, where failing the pass
    would start over from the first page an hour later.

    Raises:
        LeaderboardNotPublished: If the API answers 404 for the season.
        NoKeyAvailable: If no key was free after TOP_PAGE_KEY_ATTEMPTS.
        Exception: Any other Clash Royale API error.
    """

    for attempt in range(1, settings.TOP_PAGE_KEY_ATTEMPTS + 1):
        try:
            return await cr_api.get_path_of_legend_season_rankings(season, limit, after)
        except NoKeyAvailable as e:
            # Maintenance outlasts any short wait; the loop retries later.
            if e.reason == "maintenance" or attempt == settings.TOP_PAGE_KEY_ATTEMPTS:
                raise
            await asyncio.sleep(jitter(e.retry_after, settings.FAILURE_BACKOFF_JITTER))
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                raise LeaderboardNotPublished(season) from e
            raise
    raise AssertionError("unreachable")


def _new_entries(
    cr_api: ClashRoyaleAPI, season: str, items: list, seen: set[str], last_rank: int
) -> list[dict]:
    """The usable entries of one leaderboard page, adding their tags to seen.

    last_rank is the rank of the last entry taken so far, 0 before the first.
    """

    entries = []
    for item in items:
        if not isinstance(item, dict):
            raise InvalidLeaderboard(f"entry {item!r} in {season} is no object")
        tag = item.get("tag")
        rank = item.get("rank")
        # The rank is the top list's order and its cursor in the API, so a
        # wrong type would break both.
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise InvalidLeaderboard(f"rank {rank!r} of {tag} in {season}")
        # The tag becomes a Mongo key and a schedule member, so only
        # well-formed ones are taken, once each. The API's top list pages
        # with rank > cursor, so a rank that does not advance (a tie) would
        # hide a player at a page boundary; such an entry is left out too.
        if (
            not tag
            or not cr_api.check_tag_syntax(tag)
            or tag in seen
            or rank <= last_rank
        ):
            logger.warning(
                "Skipped leaderboard entry %r (#%s) of %s", tag, rank, season
            )
            continue
        seen.add(tag)
        last_rank = rank
        entries.append({"tag": tag, "name": item.get("name") or "Player", "rank": rank})
    return entries


async def _spread_first_syncs(
    battles: Schedule,
    profiles: Schedule,
    mongo_conn: MongoConn,
    season: str,
    capacity: Capacity | None,
) -> None:
    """Spread the first syncs of the newly tracked top players.

    Over one base interval instead of all due at once, so up to TOP_PLAYERS_N
    first syncs do not arrive as one block. This replaces the due time the
    reconciler gives them if it ran since the Mongo writes (0 for a player
    never synced, a past one for a reactivated player), but no running claim.
    Read from Mongo, so a pass that resumes after a crash spreads them too.
    Profiles are spread like the reconciler spreads players without one, and
    an already scheduled profile keeps its time.
    """

    tags = await get_top_players_awaiting_sync(mongo_conn, season)
    if not tags:
        return
    now_s = time.time()
    base_s = capacity.base_interval_s if capacity is not None else None
    base_s = base_s or settings.MIN_SYNC_INTERVAL
    await battles.reschedule_unclaimed(
        {tag: int((now_s + random.uniform(0, base_s)) * 1000) for tag in tags}
    )
    await profiles.add_many(
        {
            tag: int(
                (now_s + random.uniform(0, settings.PROFILE_FIRST_REFRESH_SPREAD))
                * 1000
            )
            for tag in tags
        }
    )


async def _retire_former_top_players(
    battles: Schedule,
    profiles: Schedule,
    mongo_conn: MongoConn,
    season: str,
    tags: list[str],
) -> int:
    """Drop the protection of every top player the list of season leaves out.

    The caller holds parking_lock.

    Returns:
        int: Number of former top players parked.
    """

    parked = 0
    for tag in await get_former_top_players(mongo_conn, season, tags):
        # Right after each player's own write, as in park_players, which only
        # narrows the window: an API add that tracks the player again between
        # the write and the removal loses its schedule entries until the next
        # reconciliation adds them back.
        if await retire_former_top_player(mongo_conn, tag):
            parked += 1
            await battles.remove(tag)
            await profiles.remove(tag)
    return parked


async def _park_over_budget(
    battles: Schedule,
    profiles: Schedule,
    mongo_conn: MongoConn,
    max_players: int | None,
) -> int:
    """Park the least viewed players until the budget holds again.

    The caller holds parking_lock. Without a published capacity the hourly
    parking check catches up, and a degraded budget parks nobody, as in
    players_to_park.

    Returns:
        int: Number of players parked.
    """

    if max_players is None or budget_degraded(max_players):
        return 0
    over_budget = await get_parkable_players_count(mongo_conn) - parking_budget(
        max_players
    )
    if over_budget <= 0:
        return 0
    return await park_players(battles, profiles, mongo_conn, over_budget)


async def roll_over(
    cr_api: ClashRoyaleAPI,
    battles: Schedule,
    profiles: Schedule,
    mongo_conn: MongoConn,
) -> bool:
    """Apply the last finished season's top list, unless it is applied already.

    An applied list is fetched and applied again once TOP_PLAYERS_N changed,
    so the protected players always match the reserved slots.

    Returns:
        bool: True if this call applied a season.

    Raises:
        LeaderboardNotPublished: If the season has no leaderboard yet.
        Exception: Any Mongo, Redis or Clash Royale API error. The next pass
            resumes from the stored list.
    """

    now = datetime.now(timezone.utc)
    season = last_finished_season_id(now)
    top_n = schedule_settings.TOP_PLAYERS_N
    stored = await get_top_season(mongo_conn, season)
    if stored is not None and stored.get("appliedTopN") == top_n:
        return False

    if stored is None or stored.get("topN") != top_n:
        if stored is None and now < _fetch_from(now):
            # The previous season's list stays applied meanwhile.
            logger.info("Waiting for the leaderboard of %s to settle", season)
            return False
        players = await fetch_leaderboard(cr_api, season)
        if not players:
            raise LeaderboardNotPublished(season)
        await save_top_season(mongo_conn, season, players, top_n)
    else:
        players = stored["players"]

    capacity = await read_capacity(battles.redis, schedule_settings.CAPACITY_MAX_AGE_S)
    max_players = capacity.max_players if capacity is not None else None
    if max_players is not None and budget_degraded(max_players):
        logger.warning(
            "TOP_PLAYERS_N (%d) leaves too little of the %d players the keys "
            "can sync: parking pauses and the players may sync late",
            schedule_settings.TOP_PLAYERS_N,
            max_players,
        )

    async with parking_lock:
        activated = await write_top_finish(mongo_conn, season, players)
        # Right after the activation, so the reconciler has little time to
        # schedule them all at once first.
        await _spread_first_syncs(battles, profiles, mongo_conn, season, capacity)
        parked = await _retire_former_top_players(
            battles, profiles, mongo_conn, season, [p["tag"] for p in players]
        )
        parked_for_budget = await _park_over_budget(
            battles, profiles, mongo_conn, max_players
        )

    await mark_top_season_applied(mongo_conn, season, top_n)
    logger.info(
        "Top players of %s applied: %d listed, %d newly tracked, %d former top "
        "players parked, %d others parked for the budget",
        season,
        len(players),
        len(activated),
        parked,
        parked_for_budget,
    )
    return True


def _fetch_from(now: datetime) -> datetime:
    """Earliest moment the last finished season's leaderboard is fetched:
    TOP_RESET_MARGIN after the computed start of the current season."""

    season_start = season_bounds(season_id_at(now))[0]
    return season_start + timedelta(seconds=settings.TOP_RESET_MARGIN)


def _until_next_check() -> float:
    """Seconds until the next pass: right when the leaderboard of a just
    finished season may be fetched, at most TOP_CHECK_INTERVAL away."""

    now = datetime.now(timezone.utc)
    fetch_from = _fetch_from(now)
    if now >= fetch_from:
        fetch_from = next_season_start(now) + timedelta(
            seconds=settings.TOP_RESET_MARGIN
        )
    return min(settings.TOP_CHECK_INTERVAL, (fetch_from - now).total_seconds())


async def top_players_loop(
    cr_api: ClashRoyaleAPI,
    battles: Schedule,
    profiles: Schedule,
    mongo_conn: MongoConn,
) -> None:
    """Check for a newly finished season until cancelled.

    The first pass runs at startup, so a new deployment tracks the last
    finished season's top players right away. Later passes run every
    TOP_CHECK_INTERVAL, and TOP_RESET_MARGIN after each season reset.
    """

    while True:
        try:
            # Every pass, as a failure must not let a rollover run on players
            # without the field: it would park every one of them.
            marked = await migrate_regular_players(mongo_conn)
            if marked:
                logger.info("Marked %d tracked players as regular", marked)
            await roll_over(cr_api, battles, profiles, mongo_conn)
            await asyncio.sleep(_until_next_check())
        except asyncio.CancelledError:
            raise
        except LeaderboardNotPublished as e:
            logger.info("No leaderboard for season %s yet, retrying later", e)
            await asyncio.sleep(settings.TOP_RETRY_DELAY)
        except InvalidLeaderboard as e:
            # Nothing was stored, so the next pass fetches the list anew.
            logger.warning("Leaderboard rejected, retrying later: %s", e)
            await asyncio.sleep(settings.TOP_RETRY_DELAY)
        except Exception:
            logger.exception("Top players rollover failed, retrying later")
            await asyncio.sleep(settings.TOP_RETRY_DELAY)
