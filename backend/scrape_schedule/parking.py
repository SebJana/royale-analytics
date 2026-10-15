"""When and how many players are parked, shared by the data scraper and the API.

Parking takes the least viewed players off both schedules while keeping their
stored data and their place in the search, so tracking near capacity makes
room instead of refusing new players. The scraper parks down to the low
watermark every hour once usage passes the high watermark. The API parks
exactly one player when an add finds the pool full. Both have to agree on the
thresholds and the spare window (see settings.py).

The order itself (views30d, then lastViewedAt, then insertedAt) is the Mongo
query in get_park_candidates.
"""

import math
from datetime import datetime, timedelta, timezone

from .settings import settings


def players_to_park(active_players: int, max_players: int) -> int:
    """Number of players the hourly check parks.

    Args:
        active_players (int): Currently tracked players.
        max_players (int): The scraper's admission limit.

    Returns:
        int: Zero at or below the high watermark, otherwise the number that
        brings usage down to the low watermark.
    """

    if max_players <= 0 or active_players <= settings.PARK_HIGH_WATERMARK * max_players:
        return 0
    return active_players - math.floor(settings.PARK_LOW_WATERMARK * max_players)


def spare_since(now: datetime | None = None) -> datetime:
    """Start of the spare window: players added or viewed after it go last."""

    return (now or datetime.now(timezone.utc)) - timedelta(
        days=settings.PARK_SPARE_DAYS
    )
