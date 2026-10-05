"""Keeps the schedules consistent with the tracked players in Mongo.

Mongo is the source of truth. The API adds and removes players in the schedules
directly, but a failed Redis write, a lost Redis volume, or players inserted
without the API would otherwise leave players unscheduled forever.
"""

import logging
import random
import time
from datetime import datetime, timezone

from mongo import MongoConn, get_tracked_players_sync_times
from scrape_schedule import Schedule
from settings import settings

logger = logging.getLogger(__name__)


def _epoch_ms(value: datetime, plus_s: float) -> int:
    # Motor returns naive datetimes that are UTC
    return int((value.replace(tzinfo=timezone.utc).timestamp() + plus_s) * 1000)


def _battle_due_ms(sync: dict) -> int:
    if sync["lastBattlesSyncAt"] is None:
        # Never synced: in front of the queue
        return 0
    # Continue the player's own rhythm instead of syncing every rebuilt
    # player at once after a Redis data loss.
    interval = sync["syncIntervalS"] or settings.MIN_SYNC_INTERVAL
    return _epoch_ms(sync["lastBattlesSyncAt"], interval)


def _profile_due_ms(sync: dict) -> int:
    if sync["lastProfileSyncAt"] is None:
        # Players without a profile, e.g. bulk inserted. Spread over
        # PROFILE_FIRST_REFRESH_SPREAD instead
        # of all due now, which would put every one of them into one block
        # (intervals.py, "Spreading due times"). Never score 0: profiles never
        # jump ahead of due battle syncs unless they are late by more than
        # PROFILE_MAX_LATENESS. Players added through the API store their
        # profile on adding, so they do not get here.
        spread_s = random.uniform(0, settings.PROFILE_FIRST_REFRESH_SPREAD)
        return int((time.time() + spread_s) * 1000)
    interval = sync["profileSyncIntervalS"] or settings.PROFILE_MIN_INTERVAL
    return _epoch_ms(sync["lastProfileSyncAt"], interval)


async def reconcile_schedules(
    battles: Schedule, profiles: Schedule, mongo_conn: MongoConn
) -> dict[str, dict]:
    """Reconcile both schedules with the tracked players.

    Both schedules are read BEFORE Mongo. The API writes Mongo first and the
    schedules second, so a player added during this run is either already in
    the schedule snapshot or already in Mongo, and is never removed as stale.

    Returns:
        dict[str, dict]: The tracked players with their sync state (see
            get_tracked_players_sync_times), reused for the capacity estimate.
    """

    battles_snapshot = await battles.scheduled_tags()
    profiles_snapshot = await profiles.scheduled_tags()
    tracked = await get_tracked_players_sync_times(mongo_conn)

    results = {}
    for name, schedule, snapshot, due_ms in (
        ("battles", battles, battles_snapshot, _battle_due_ms),
        ("profiles", profiles, profiles_snapshot, _profile_due_ms),
    ):
        missing = {
            tag: due_ms(sync) for tag, sync in tracked.items() if tag not in snapshot
        }
        stale = snapshot - tracked.keys()
        # NX keeps a due time the API set between the reads above.
        await schedule.add_many(missing)
        await schedule.remove(*stale)
        results[name] = (len(missing), len(stale))

    for name, (added, removed) in results.items():
        if added or removed:
            logger.info(
                "%s schedule reconciled: %d players added, %d removed",
                name.capitalize(),
                added,
                removed,
            )
    return tracked
