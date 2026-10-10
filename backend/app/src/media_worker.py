"""Keeps the media Redis stocked with pre-rendered Fruit Buzz cards and CAPTCHAs.

Runs as its own service (media-worker in docker-compose.yml), one instance,
from the API image. It is the only producer of the media pools
(helpers/media_pool); the API only consumes them, so no request waits for a
render and rendering never runs on the API's event loop.

Each pass runs the three producer steps:
1. fill_card_pools: one card for the emptiest combination below its size.
2. replace_card: the card due first is swapped for a fresh one.
3. produce_captchas: the CAPTCHA stock is topped up, one batch at most.
Cards and CAPTCHAs every pass, so a CAPTCHA flood cannot starve the cards.
With nothing to do it sleeps until the next card is due or the budget allows
the next render, at most MEDIA_WORKER_IDLE_SECONDS.

The work follows the visitors. Stocked media has no TTL and only delivery
starts its clock, so without visitors nothing comes due and the worker only
checks the pools once a second. With visitors it renders what they used up,
up to MEDIA_WORKER_MAX_CARDS_PER_MINUTE and MEDIA_WORKER_MAX_CAPTCHAS_PER_MINUTE.
Past those, visitors share cards for longer and CAPTCHA claims get 503,
so traffic-driven rendering stays within the budgets, in a container of its own (cpus: 1).
Only refilling empty slots (a fresh or wiped media Redis) runs unbudgeted,
and the pool size bounds it.
"""

import logging
import time
from dataclasses import dataclass

import redis

from core.settings import settings
from helpers.media_pool.producer import (
    RenderBudget,
    captcha_stock,
    card_pool_sizes,
    delivered_cards,
    fill_card_pools,
    next_retirement,
    produce_captchas,
    replace_card,
)

logger = logging.getLogger("media_worker")

# How often the worker logs what it produced and how full the pools are.
STATUS_INTERVAL_SECONDS = 60  # seconds

# Wait before retrying after the media Redis was unreachable or full.
RECONNECT_DELAY_SECONDS = 2  # seconds


@dataclass
class Budgets:
    """Render budgets of the work visitors cause.

    Attributes:
        cards (RenderBudget): Card replacements.
        captchas (RenderBudget): CAPTCHA renders.
    """

    cards: RenderBudget
    captchas: RenderBudget


def run_pass(conn: redis.Redis, budgets: Budgets, counts: dict[str, int]) -> bool:
    """Run every producer step once.

    Args:
        conn (redis.Redis): Client of the media Redis.
        budgets (Budgets): Render budgets, spent in place.
        counts (dict[str, int]): Running totals, updated in place.

    Returns:
        bool: Whether any step did work.
    """
    filled = fill_card_pools(conn)
    replaced = replace_card(conn, budgets.cards)
    captchas = produce_captchas(conn, budgets.captchas)
    counts["filled"] += filled
    counts["replaced"] += replaced
    counts["captchas"] += captchas
    return bool(filled or replaced or captchas)


def idle_seconds(conn: redis.Redis, budgets: Budgets) -> float:
    """Return how long to sleep until the next card is due or a blocked
    render gets budget.

    At most the idle interval, so a pool drained meanwhile is noticed soon.

    Args:
        conn (redis.Redis): Client of the media Redis.
        budgets (Budgets): Render budgets, to wait for a blocked render.

    Returns:
        float: Seconds, at least 0.05.
    """
    waits = [settings.MEDIA_WORKER_IDLE_SECONDS]
    upcoming = next_retirement(conn)
    if upcoming is not None:
        waits.append(max(upcoming - time.time(), budgets.cards.wait_seconds()))
    if captcha_stock(conn) < settings.CAPTCHA_POOL_SIZE:
        waits.append(budgets.captchas.wait_seconds())
    return max(0.05, min(waits))


def log_status(conn: redis.Redis, counts: dict[str, int]) -> None:
    """Log the renders since the last status and the pool levels.

    Args:
        conn (redis.Redis): Client of the media Redis.
        counts (dict[str, int]): Running totals, reset afterwards.
    """
    sizes = card_pool_sizes(conn).values()
    logger.info(
        "Last %ss: %s cards filled, %s replaced, %s CAPTCHAs rendered | "
        "card pools %s-%s of %s, %s in service, CAPTCHA stock %s of %s",
        STATUS_INTERVAL_SECONDS,
        counts["filled"],
        counts["replaced"],
        counts["captchas"],
        min(sizes),
        max(sizes),
        settings.FRUIT_BUZZ_CARD_VARIATIONS_PER_COMBINATION,
        delivered_cards(conn),
        captcha_stock(conn),
        settings.CAPTCHA_POOL_SIZE,
    )
    counts.update(filled=0, replaced=0, captchas=0)


def main() -> None:
    """Run the producer loop until the process stops."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    conn = redis.Redis(
        host=settings.MEDIA_REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        socket_connect_timeout=3,
        socket_timeout=5,
    )
    budgets = Budgets(
        cards=RenderBudget(settings.MEDIA_WORKER_MAX_CARDS_PER_MINUTE),
        captchas=RenderBudget(settings.MEDIA_WORKER_MAX_CAPTCHAS_PER_MINUTE),
    )
    counts = {"filled": 0, "replaced": 0, "captchas": 0}
    next_status = time.monotonic() + STATUS_INTERVAL_SECONDS
    logger.info("Media worker started")
    while True:
        try:
            if not run_pass(conn, budgets, counts):
                time.sleep(idle_seconds(conn, budgets))
            if time.monotonic() >= next_status:
                log_status(conn, counts)
                next_status = time.monotonic() + STATUS_INTERVAL_SECONDS
        except redis.RedisError as error:
            # The pools are rebuilt from scratch if the media Redis restarted.
            # The message can quote the command, PNG bytes included; the
            # reason sits at its end.
            logger.warning(
                "Media Redis write failed: %s: ...%s",
                type(error).__name__,
                str(error)[-120:],
            )
            time.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    main()
