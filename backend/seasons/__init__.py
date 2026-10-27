"""Clash Royale season boundaries, shared by the API and the data scraper.

A season with id "YYYY-MM" starts on the first Monday of that month and ends
at the start of the next month's season. Supercell documents the day
(https://support.supercell.com/clash-royale/en/articles/seasons.html), not the
hour: SEASON_RESET_HOUR_UTC is an assumption backed by ranked battles around
one reset, so battles within hours of a reset can land in the wrong season if
it is off. The windows are an approximation of the real seasons in that sense.

The Clash Royale API supplies no usable boundary timestamps, so the backend
owns them: every client resolves a season id to the same UTC window, and the
stats queries match that window directly on the indexed battleTime. The data
scraper derives the last finished season from the same rule to fetch its
leaderboard, so both services have to agree on it; a copy in each would drift.

Windows are start-inclusive and end-exclusive, so consecutive seasons share
their boundary instant and no battle belongs to two of them.
"""

import re
from datetime import datetime, timedelta, timezone

# Seasons start on the first Monday of a month at this UTC hour. Supercell
# publishes the day but not the hour. Ranked battles around the October 2026
# reset point to 09:00 UTC, the logs bracket it between 08:25 and 21:19 UTC.
# Kept until a source or more resets say otherwise.
SEASON_RESET_HOUR_UTC = 9

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


def format_season_id(year: int, month: int) -> str:
    """The "YYYY-MM" id of the season starting in the given month."""
    return f"{year:04d}-{month:02d}"


def shift_month(year: int, month: int, months: int) -> tuple[int, int]:
    """The year and month that lie the given number of months away."""
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def _rule_start(year: int, month: int) -> datetime:
    """The first Monday of the month at the reset hour, in UTC."""
    first = datetime(year, month, 1, SEASON_RESET_HOUR_UTC, tzinfo=timezone.utc)
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
    return _rule_start(year, month), _rule_start(*shift_month(year, month, 1))


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
    candidate = format_season_id(utc.year, utc.month)
    # The days before the month's first Monday still belong to the season of
    # the previous month
    if utc < season_bounds(candidate)[0]:
        return format_season_id(*shift_month(utc.year, utc.month, -1))
    return candidate


def next_season_start(now: datetime | None = None) -> datetime:
    """Start of the season after the current one, as a timezone-aware UTC time.

    Args:
        now (datetime | None): Timezone-aware reference time, the current
            time by default.

    Returns:
        datetime: The moment the current season ends.
    """
    return season_bounds(season_id_at(now or datetime.now(timezone.utc)))[1]


def last_finished_season_id(now: datetime | None = None) -> str:
    """Id of the season before the current one, the newest with a final leaderboard.

    Args:
        now (datetime | None): Timezone-aware reference time, the current
            time by default.

    Returns:
        str: The season id as "YYYY-MM".
    """
    current = season_id_at(now or datetime.now(timezone.utc))
    return format_season_id(*shift_month(*parse_season_id(current), -1))


__all__ = [
    "SEASON_RESET_HOUR_UTC",
    "parse_season_id",
    "format_season_id",
    "shift_month",
    "season_bounds",
    "season_id_at",
    "next_season_start",
    "last_finished_season_id",
]
