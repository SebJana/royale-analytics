"""Clash Royale season catalogue.

A season with id "YYYY-MM" starts on the first Monday of that month and ends
at the start of the next month's season. Supercell documents the day
(https://support.supercell.com/clash-royale/en/articles/seasons.html), not the
hour: SEASON_RESET_HOUR_UTC is an assumption backed by ranked battles around
one reset, so battles within hours of a reset can land in the wrong season if
it is off. The windows are an approximation of the real seasons in that sense.

The Clash Royale API supplies no usable boundary timestamps, so the backend
owns them: every client resolves a season id to the same UTC window, and the
stats queries match that window directly on the indexed battleTime.

Windows are start-inclusive and end-exclusive, so consecutive seasons share
their boundary instant and no battle belongs to two of them.
"""

import re
from datetime import datetime, timedelta, timezone

from core.settings import settings

# ASCII digits only: \d would also accept other scripts' digits
SEASON_ID_PATTERN = re.compile(r"([0-9]{4})-([0-9]{2})")


def parse_season_id(season_id: str) -> tuple[int, int]:
    """Split a season id into its year and month.

    Args:
        season_id (str): Season id as "YYYY-MM", e.g. "2026-09".

    Returns:
        tuple[int, int]: The year and the month (1-12).

    Raises:
        ValueError: For an id that is not in the "YYYY-MM" format or names no
            month.
    """
    parsed = SEASON_ID_PATTERN.fullmatch(season_id)
    if not parsed or not 1 <= int(parsed[2]) <= 12:
        raise ValueError(f"Season {season_id!r} is not in the YYYY-MM format")
    return int(parsed[1]), int(parsed[2])


def _season_id(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift_month(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def _rule_start(year: int, month: int) -> datetime:
    """The first Monday of the month at the reset hour, in UTC."""
    first = datetime(
        year, month, 1, settings.SEASON_RESET_HOUR_UTC, tzinfo=timezone.utc
    )
    # weekday() is 0 on Mondays
    return first + timedelta(days=(7 - first.weekday()) % 7)


def season_bounds(season_id: str) -> tuple[datetime, datetime]:
    """UTC window of a season.

    Args:
        season_id (str): Season id as "YYYY-MM".

    Returns:
        tuple[datetime, datetime]: Start (inclusive) and end (exclusive) as
            timezone-aware UTC datetimes.

    Raises:
        ValueError: For a malformed season id.
    """
    year, month = parse_season_id(season_id)
    return _rule_start(year, month), _rule_start(*_shift_month(year, month, 1))


def season_id_at(moment: datetime) -> str:
    """Id of the season a moment belongs to.

    Args:
        moment (datetime): A timezone-aware datetime.

    Returns:
        str: The season id as "YYYY-MM".

    Raises:
        ValueError: For a naive datetime, which astimezone() would read in the
            host's timezone.
    """
    if moment.utcoffset() is None:
        raise ValueError("moment has to be timezone-aware")
    utc = moment.astimezone(timezone.utc)
    candidate = _season_id(utc.year, utc.month)
    # The days before the month's first Monday still belong to the season of
    # the previous month
    if utc < season_bounds(candidate)[0]:
        return _season_id(*_shift_month(utc.year, utc.month, -1))
    return candidate


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
        season_id = _season_id(*_shift_month(year, month, -offset))
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
