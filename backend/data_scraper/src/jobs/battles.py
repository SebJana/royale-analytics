"""Battle sync of a single player.

Fetches the battle log, keeps only battles newer than the player's stored
watermark (lastBattleTime), writes them, and reports when the player is due
again. Retries happen through the schedule, never inside the job: a failing
player is acknowledged with a longer delay, so it cannot block a worker or
consume requests in a tight loop.
"""

import logging
from datetime import datetime, timezone

import httpx

from api_key_store import NoKeyAvailable, KeyStoreUnavailable
from clash_royale_api import ClashRoyaleAPI, ClashRoyaleMaintenanceError
from clean import clean_battle_log_list, get_player_name
from game_modes import UniqueGameModes
from intervals import (
    failure_backoff,
    next_battle_interval,
    not_found_delay,
    scheduled_battle_delay,
)
from jobs.common import JobResult, pool_level_result
from mongo import (
    MongoConn,
    deactivate_tracked_player,
    get_player_sync_state,
    insert_battles,
    record_battle_sync,
    record_battle_sync_failure,
)
from settings import settings

logger = logging.getLogger(__name__)


async def _failed(
    mongo_conn: MongoConn,
    player_tag: str,
    base_interval_s: float,
    not_found: bool = False,
) -> JobResult:
    """Count a player level failure and decide when to try again."""

    counters = await record_battle_sync_failure(
        mongo_conn, player_tag, not_found=not_found
    )
    if not not_found:
        return JobResult(
            "failed", failure_backoff(counters.get("consecutiveFailures", 1))
        )

    not_found_count = counters.get("consecutiveNotFound", 1)
    first_not_found = counters.get("firstNotFoundAt")
    if first_not_found is not None and not_found_count >= (
        settings.NOT_FOUND_DEACTIVATE_COUNT
    ):
        # Motor returns naive datetimes that are UTC
        missing_for = (
            datetime.now(timezone.utc) - first_not_found.replace(tzinfo=timezone.utc)
        ).total_seconds()
        # Both conditions are required: several 404s in a row rule out a
        # single glitch, the time span rules out a short API outage.
        if missing_for >= settings.NOT_FOUND_MIN_SPAN:
            await deactivate_tracked_player(mongo_conn, player_tag, reason="not_found")
            logger.warning(
                "Untracked %s after %d consecutive 404s over %.0f hours",
                player_tag,
                not_found_count,
                missing_for / 3600,
            )
            return JobResult("deactivated", 0)

    return JobResult("not_found", not_found_delay(base_interval_s, not_found_count))


def _possible_gap(
    player_tag: str,
    battle_logs: list,
    cleaned: list,
    new_count: int,
    last_battle_time: datetime | None,
) -> bool:
    """Check whether the battle log may have overflowed since the last sync.

    Every battle of a full log being new means it no longer reaches back to
    the watermark, so battles in between may be lost. Logged and counted to
    check MAX_SYNC_INTERVAL against real play.
    """

    possible_gap = (
        last_battle_time is not None
        and new_count == len(cleaned)
        and len(battle_logs) >= settings.BATTLE_LOG_SIZE
    )
    if possible_gap:
        logger.warning(
            "Possible battle gap for %s between %s and %s",
            player_tag,
            last_battle_time,
            min(battle["battleTime"] for battle in cleaned),
        )
    return possible_gap


async def sync_player_battles(
    player_tag: str,
    cr_api: ClashRoyaleAPI,
    mongo_conn: MongoConn,
    mode_store: UniqueGameModes,
    base_interval_s: float,
    lateness_s: float = 0.0,
) -> JobResult:
    """Fetch, validate, clean, and persist the new battles of one player.

    Args:
        player_tag (str): Player tag (e.g., "#YYRJQY28").
        cr_api (ClashRoyaleAPI): API client backed by the scraper key pool.
        mongo_conn (MongoConn): Mongo connection used to read state and write data.
        mode_store (UniqueGameModes): Collects game modes until the next flush.
        base_interval_s (float): Current capacity based battle interval.
        lateness_s (float): How long the player waited after it became due.
            A long wait spreads the next due time (scheduled_battle_delay).

    Returns:
        JobResult: What happened and when the player is due again.

    Raises:
        Exception: Mongo errors propagate; the caller retries the player later.
    """

    state = await get_player_sync_state(mongo_conn, player_tag)
    if not state or not state.get("active"):
        # Untracked while it was still scheduled. The reconciler would remove
        # it as well, but there is no reason to spend a request on it first.
        return JobResult("inactive", 0)

    try:
        battle_logs = await cr_api.get_player_battle_logs(player_tag=player_tag)

    except (
        ClashRoyaleMaintenanceError,
        NoKeyAvailable,
        KeyStoreUnavailable,
        httpx.HTTPStatusError,
    ) as e:
        pool_result = pool_level_result(e)
        if pool_result is not None:
            return pool_result
        # Only HTTP errors that belong to the player get here
        code = e.response.status_code if e.response is not None else 0
        logger.warning("HTTP %s for %s", code, player_tag)
        return await _failed(
            mongo_conn, player_tag, base_interval_s, not_found=code == 404
        )

    except httpx.RequestError as e:
        logger.warning("Network error for %s: %r", player_tag, e)
        return await _failed(mongo_conn, player_tag, base_interval_s)

    previous_interval = state.get("syncIntervalS")
    # No interval yet: added (or reactivated) due immediately, so this sync
    # ran in the batch it was added with, whatever its claim lateness says.
    first_sync = previous_interval is None

    # Checked before the emptiness test: None or {} would otherwise count as
    # a successful sync without battles and reset the failure counters.
    if not isinstance(battle_logs, list):
        logger.error("Battle log of %s is not a list", player_tag)
        return await _failed(mongo_conn, player_tag, base_interval_s)

    if not battle_logs:
        # A player without recent battles is still a successful sync.
        interval = next_battle_interval(base_interval_s, previous_interval, 0)
        await record_battle_sync(mongo_conn, player_tag, None, None, 0, interval)
        return JobResult(
            "synced", scheduled_battle_delay(interval, lateness_s, first_sync)
        )

    # Malformed entries are skipped inside; only a log without a single usable
    # battle counts as a failure.
    cleaned = clean_battle_log_list(battle_logs, player_tag=player_tag)
    if not cleaned:
        logger.error("Battle log of %s has no usable battle", player_tag)
        return await _failed(mongo_conn, player_tag, base_interval_s)

    # Collect modes from every fetched battle, not only the new ones. Modes
    # lost in a failed flush are then seen again on the next sync.
    for battle in cleaned:
        if battle.get("gameMode"):
            mode_store.add(battle["gameMode"])

    # Only battles after the watermark are new. Without this filter nearly
    # every insert would be a duplicate rejected by the unique index.
    # NOTE Assumes the battle log never adds a battle older than one already
    # fetched. A battle that appears late, with a battleTime at or before the
    # watermark, is skipped for good.
    last_battle_time = state.get("lastBattleTime")
    new_battles = [
        battle
        for battle in cleaned
        if last_battle_time is None or battle["battleTime"] > last_battle_time
    ]
    inserted = await insert_battles(mongo_conn, new_battles)

    # Activity and the cache version follow the battles past the watermark,
    # not the insert count. If a previous attempt inserted them and then
    # failed to record the sync, the retry inserts only duplicates, but the
    # cached statistics still predate these battles.
    new_count = len(new_battles)
    interval = next_battle_interval(base_interval_s, previous_interval, new_count)

    possible_gap = _possible_gap(
        player_tag, battle_logs, cleaned, new_count, last_battle_time
    )

    # The watermark comes from all fetched battles, not only the new ones.
    # It moves past battles the cleaner skipped as malformed, so those
    # are not retried, even after a cleaner fix. Holding the watermark below
    # them would resend every newer battle on each sync and make the player
    # look active (lastSyncNewBattles) until the entry leaves the log.
    newest = max(battle["battleTime"] for battle in cleaned)
    # Users find players by name, which can change at any time. Without a new
    # battle the log only holds the name from before the last sync, which
    # would overwrite a newer name stored by a profile refresh.
    player_name = (
        get_player_name(new_battles, player_tag=player_tag) if new_battles else None
    )
    await record_battle_sync(
        mongo_conn, player_tag, newest, player_name, new_count, interval
    )

    # Mongo stores the planned interval; only the due time is spread.
    return JobResult(
        "synced",
        scheduled_battle_delay(interval, lateness_s, first_sync),
        inserted,
        possible_gap,
    )
