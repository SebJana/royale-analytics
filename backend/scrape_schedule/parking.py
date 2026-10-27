"""When and how many players are parked, shared by the data scraper and the API.

Parking takes the least viewed players off both schedules while keeping their
stored data and their place in the search, so tracking near capacity makes
room instead of refusing new players. The scraper parks down to the low
watermark every hour once usage passes the high watermark. The API parks
exactly one player when an add finds the pool full. Both have to agree on the
thresholds, the spare window and the budget (see settings.py).

Only parkable players count: the active ones without topFinish. The top
players of the last season are never parked, and TOP_PLAYERS_N slots of
max_players are kept for them whether or not they are tracked yet. While the
rest of max_players is below PARK_MIN_BUDGET_SHARE (budget_degraded), no path
parks and the API refuses new players instead.

The order itself (views30d, then lastViewedAt, then insertedAt) is the Mongo
query in get_park_candidates.
"""

import math
from datetime import datetime, timedelta, timezone

from .settings import settings


def parking_budget(max_players: int) -> int:
    """Most parkable players that fit next to the reserved top players.

    Args:
        max_players (int): The scraper's admission limit.

    Returns:
        int: max_players - TOP_PLAYERS_N, never below zero.
    """

    return max(max_players - settings.TOP_PLAYERS_N, 0)


def budget_degraded(max_players: int) -> bool:
    """Whether the budget is too small to park for.

    Args:
        max_players (int): The scraper's admission limit.

    Returns:
        bool: True if max_players - TOP_PLAYERS_N is below
        PARK_MIN_BUDGET_SHARE of max_players, or no key is usable.
    """

    if max_players <= 0:
        return True
    return parking_budget(max_players) < settings.PARK_MIN_BUDGET_SHARE * max_players


def players_to_park(parkable_players: int, max_players: int) -> int:
    """Number of players the hourly check parks.

    Args:
        parkable_players (int): Active players without topFinish.
        max_players (int): The scraper's admission limit.

    Returns:
        int: Zero at or below the high watermark of the budget, or while the
        budget is degraded, otherwise the number that brings usage down to
        the low watermark.
    """

    if budget_degraded(max_players):
        return 0
    budget = parking_budget(max_players)
    if parkable_players <= settings.PARK_HIGH_WATERMARK * budget:
        return 0
    return parkable_players - math.floor(settings.PARK_LOW_WATERMARK * budget)


def spare_since(now: datetime | None = None) -> datetime:
    """Start of the spare window: players added or viewed after it go last."""

    return (now or datetime.now(timezone.utc)) - timedelta(
        days=settings.PARK_SPARE_DAYS
    )
