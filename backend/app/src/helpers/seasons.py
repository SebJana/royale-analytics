"""Clash Royale season catalogue.

The season boundaries themselves live in the shared seasons package, which
the data scraper uses as well; this module adds what only the API offers: the
list of selectable seasons, bounded by SEASON_FIRST_ID.
"""

from datetime import datetime, timezone

from core.settings import settings
from seasons import (
    format_season_id,
    parse_season_id,
    season_bounds,
    season_id_at,
    shift_month,
)

__all__ = ["parse_season_id", "season_bounds", "season_id_at", "recent_seasons"]


def recent_seasons(limit: int, now: datetime | None = None) -> list[dict]:
    """The newest seasons up to the current one, never older than SEASON_FIRST_ID.

    Args:
        limit (int): Maximum number of seasons returned.
        now (datetime | None): Reference time, the current time by default.

    Returns:
        list[dict]: {"id", "start", "end", "isCurrent"} per season, newest
            first. start and end are UTC datetimes, end exclusive.
    """
    current = season_id_at(now or datetime.now(timezone.utc))
    year, month = parse_season_id(current)
    seasons = []
    for offset in range(limit):
        season_id = format_season_id(*shift_month(year, month, -offset))
        # Zero-padded ids sort like the seasons themselves
        if season_id < settings.SEASON_FIRST_ID:
            break
        start, end = season_bounds(season_id)
        seasons.append(
            {
                "id": season_id,
                "start": start,
                "end": end,
                "isCurrent": season_id == current,
            }
        )
    return seasons
