"""Periodic explore ranking from the counted profile views.

The API counts views into daily buckets (player_views) as they happen. This
job sums the buckets of the ranking window onto the players (views30d) and
numbers the explore list (exploreRank), which the API pages through and
derives the popular badge from. Summing on read would scan 30 buckets per
player for every explore page.

This job is the only writer of views30d and exploreRank. The writes are
not atomic across players: while a ranking is written, or after one failed
halfway, readers can see new counts next to old ranks, or a rank twice,
until the next complete pass. Acceptable for a discovery list; the explore
pages drop repeated players. See docs/profile-views.md.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from mongo import (
    MongoConn,
    ensure_player_views_indexes,
    get_player_view_baselines,
    get_player_views_on,
    sum_player_views,
    write_view_ranking,
)
from settings import settings

logger = logging.getLogger(__name__)


def _utc_midnight(moment: datetime) -> datetime:
    """The UTC day of moment, as its bucket key in player_views."""

    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


async def rank_views(mongo_conn: MongoConn) -> None:
    """Store the windowed view counts and the explore ranking once.

    Raises:
        Exception: Any Mongo error; the loop retries later.
    """

    today = _utc_midnight(datetime.now(timezone.utc))
    # The window includes today, so it starts VIEWS_WINDOW_DAYS - 1 days back.
    since = today - timedelta(days=settings.VIEWS_WINDOW_DAYS - 1)
    views = await sum_player_views(mongo_conn, since)
    ranked = await write_view_ranking(mongo_conn, views, settings.EXPLORE_MIN_VIEWS)
    logger.info(
        "View ranking: %d viewed players in %d days, %d ranked",
        len(views),
        settings.VIEWS_WINDOW_DAYS,
        ranked,
    )

    # A sudden jump is how faked views usually look. Logged for a look, never
    # acted on: a player going viral looks the same. exploreHidden on the
    # player document removes one by hand. The baseline is the average over
    # the earlier days that have views, so a young database or a gap in the
    # data does not make every player look like a spike.
    today_views = await get_player_views_on(mongo_conn, today)
    baselines = await get_player_view_baselines(mongo_conn, since, today)
    for tag, visitors in today_views.items():
        earlier, days = baselines.get(tag, (0, 0))
        if days < settings.VIEWS_SPIKE_MIN_DAYS:
            continue
        daily_average = earlier / days
        if (
            visitors >= settings.VIEWS_SPIKE_MIN
            and visitors > settings.VIEWS_SPIKE_FACTOR * daily_average
        ):
            logger.warning(
                "View spike for %s: %d visitors today, %.1f a day on average",
                tag,
                visitors,
                daily_average,
            )


async def views_ranking_loop(mongo_conn: MongoConn) -> None:
    """Rank the views every VIEWS_RANKING_INTERVAL until cancelled.

    The first pass runs at startup, so a restart never leaves the ranking
    older than one interval.
    """

    try:
        await ensure_player_views_indexes(mongo_conn)
    except Exception:
        # The ranking only sums the buckets, which works without the indexes.
        # The API also creates them at startup and logs its own failure.
        logger.exception("Player views indexes could not be created")
    while True:
        try:
            await rank_views(mongo_conn)
            await asyncio.sleep(settings.VIEWS_RANKING_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Players keep what the failed pass wrote, or the previous
            # ranking where it did not get to them (see the module docstring).
            logger.exception("View ranking failed, retrying later")
            await asyncio.sleep(settings.VIEWS_RANKING_RETRY_DELAY)
