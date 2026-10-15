"""Hourly parking of the least viewed players near capacity.

Above the high watermark, players are parked in parking order until usage is
at the low watermark, so adds keep finding room without parking one player
per add. Below it, nobody is parked: an emptier system tracks everyone.

Mongo is written first, then the schedules, one player at a time: a player
tracked again by the API right after its own parking must not lose the
schedule entries the API just added to a removal that waited for the whole
batch. A failed schedule removal only delays the stop until the player's next
claim (the battle job drops inactive players) or the next reconciliation.
"""

import asyncio
import logging

from mongo import (
    PARKED_REASON,
    MongoConn,
    deactivate_tracked_player,
    get_park_candidates,
    get_players_count,
)
from scrape_schedule import (
    Schedule,
    players_to_park,
    read_capacity,
    schedule_settings,
    spare_since,
)
from settings import settings

logger = logging.getLogger(__name__)


async def park_over_capacity(
    battles: Schedule, profiles: Schedule, mongo_conn: MongoConn
) -> int:
    """Park players once if usage is above the high watermark.

    Returns:
        int: Number of players parked.

    Raises:
        Exception: Any Mongo or Redis error; the loop retries later.
    """

    # The published estimate, not one computed here: it is what the API's
    # admission compares with, so both sides use the same limit.
    capacity = await read_capacity(battles.redis, schedule_settings.CAPACITY_MAX_AGE_S)
    if capacity is None or capacity.max_players is None:
        return 0
    active = await get_players_count(mongo_conn)
    count = players_to_park(active, capacity.max_players)
    if count == 0:
        return 0

    parked = 0
    for tag in await get_park_candidates(mongo_conn, count, spare_since()):
        # Zero if the API untracked or parked the player since the read
        if await deactivate_tracked_player(mongo_conn, tag, PARKED_REASON):
            parked += 1
            await battles.remove(tag)
            await profiles.remove(tag)
    logger.info(
        "Parked %d players: %d active of at most %d",
        parked,
        active,
        capacity.max_players,
    )
    return parked


async def parking_loop(
    battles: Schedule, profiles: Schedule, mongo_conn: MongoConn
) -> None:
    """Run the parking check every PARK_CHECK_INTERVAL until cancelled.

    The first check waits one interval, so the reconciler has published a
    fresh capacity estimate for the current keys before anyone is parked.
    """

    while True:
        await asyncio.sleep(settings.PARK_CHECK_INTERVAL)
        try:
            await park_over_capacity(battles, profiles, mongo_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Players already parked stay parked; the next check continues.
            logger.exception("Parking check failed")
