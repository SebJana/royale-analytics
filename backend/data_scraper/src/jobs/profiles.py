"""Profile snapshot refresh of a single player.

The API serves profiles from these snapshots instead of calling the Clash
Royale API per page view. A profile may be far staler than the battle data, so
it runs on its own schedule with a long period and a low priority.
"""

import logging

import httpx

from api_key_store import NoKeyAvailable, KeyStoreUnavailable
from clash_royale_api import ClashRoyaleAPI, ClashRoyaleMaintenanceError
from intervals import jitter, next_profile_interval
from jobs.common import JobResult, pool_level_result
from mongo import MongoConn, get_player_sync_state, save_player_profile
from settings import settings

logger = logging.getLogger(__name__)

# TODO only store info needed for this app
# drop achievements, badges, card level progress, etc.
# If the storage is negligible still dont serve that info to the
# frontend to make the payload smaller and faster
# Maybe CR picked favorite deck storing to compare with what this analytics
# will determine as favorite deck


def _retry_delay() -> float:
    # Failures usually hit many profiles at once (API errors, timeouts).
    return jitter(settings.PROFILE_RETRY_DELAY, settings.FAILURE_BACKOFF_JITTER)


async def refresh_player_profile(
    player_tag: str,
    cr_api: ClashRoyaleAPI,
    mongo_conn: MongoConn,
    base_interval_s: float,
) -> JobResult:
    """Fetch the player's profile and store it as the latest snapshot.

    Failures only delay the next attempt. They do not count towards the
    player's failure or not-found counters; the battle job owns those, so a
    deleted account is untracked through one path only.

    Args:
        player_tag (str): Player tag (e.g., "#YYRJQY28").
        cr_api (ClashRoyaleAPI): API client backed by the scraper key pool.
        mongo_conn (MongoConn): Mongo connection used to read state and write data.
        base_interval_s (float): Current capacity based battle interval. Under
            load it stretches the profile interval as well.

    Returns:
        JobResult: What happened and when the profile is due again.

    Raises:
        Exception: Mongo errors propagate; the caller retries the player later.
    """

    state = await get_player_sync_state(mongo_conn, player_tag)
    if not state or not state.get("active"):
        return JobResult("inactive", 0)

    try:
        profile = await cr_api.get_player_info(player_tag)

    except (
        ClashRoyaleMaintenanceError,
        NoKeyAvailable,
        KeyStoreUnavailable,
        httpx.HTTPStatusError,
    ) as e:
        pool_result = pool_level_result(e)
        if pool_result is not None:
            return pool_result
        code = e.response.status_code if e.response is not None else 0
        logger.warning("HTTP %s for the profile of %s", code, player_tag)
        return JobResult("failed", _retry_delay())

    except httpx.RequestError as e:
        logger.warning("Network error for the profile of %s: %r", player_tag, e)
        return JobResult("failed", _retry_delay())

    if not isinstance(profile, dict) or not profile.get("name"):
        # Keep the previous snapshot rather than replacing it with an
        # unusable response.
        logger.error("Profile of %s couldn't be used", player_tag)
        return JobResult("failed", _retry_delay())

    # Battles stored after the previous refresh mean the profile stats changed
    last_battle = state.get("lastBattleTime")
    last_refresh = state.get("lastProfileSyncAt")
    played = last_refresh is None or (
        last_battle is not None and last_battle > last_refresh
    )
    interval = next_profile_interval(
        base_interval_s, state.get("profileSyncIntervalS"), played
    )

    await save_player_profile(mongo_conn, player_tag, profile, interval)
    # Mongo stores the planned interval; only the due time is jittered, so
    # profiles refreshed in one batch drift apart (intervals.py, "Spreading
    # due times").
    return JobResult("synced", jitter(interval, settings.INTERVAL_JITTER))
