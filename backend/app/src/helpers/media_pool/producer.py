"""Worker side of the media pools: renders, publishes and replaces, the only writer.

The three producer steps (``fill_card_pools``, ``replace_card``,
``produce_captchas``) are what media_worker.py runs in a loop. Everything
below them is the budget and the Redis bookkeeping they share.
"""

import json
import time
import uuid
from dataclasses import dataclass, field

import redis

from core.settings import settings
from helpers.generate_captcha import generate_captcha_image, generate_captcha_string
from helpers.fruit_buzz_card import AVAILABLE_FRUITS, FRUIT_POSITIONS, create_card
from helpers.fruit_buzz_rendering.models import FruitBuzzCard

from . import (
    CAPTCHA_POOL_KEY,
    CARD_RETIRE_KEY,
    captcha_key,
    card_pool_key,
    card_retire_member,
    card_template_key,
)

# CAPTCHAs rendered per pass (~8 ms each), so refilling a drained stock does
# not delay card replacements for long.
CAPTCHA_BATCH = 25

# Every fruit/amount pair; each has a card pool of its own.
COMBINATIONS = [
    (fruit, amount) for fruit in AVAILABLE_FRUITS for amount in FRUIT_POSITIONS
]


@dataclass
class RenderBudget:
    """Token bucket for renders that traffic causes.

    Refills ``per_minute`` tokens a minute and holds at most that many, so a
    quiet stretch allows one burst of a minute's worth, never more.

    Attributes:
        per_minute (float): Renders a minute, and the burst size.
        tokens (float): Renders allowed right now, fractions included.
        refilled_at (float): Monotonic time of the last refill.
    """

    per_minute: float
    tokens: float = field(init=False)
    refilled_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.per_minute
        self.refilled_at = time.monotonic()

    def available(self) -> int:
        """Return the whole renders the budget allows right now.

        Returns:
            int: Renders allowed, after adding what the elapsed time earned.
        """
        now = time.monotonic()
        earned = (now - self.refilled_at) * self.per_minute / 60
        self.tokens = min(self.per_minute, self.tokens + earned)
        self.refilled_at = now
        return int(self.tokens)

    def spend(self, renders: int = 1) -> None:
        """Take renders from the budget.

        Args:
            renders (int): Renders done, at most ``available()``.
        """
        self.tokens -= renders

    def wait_seconds(self) -> float:
        """Return how long until the budget allows the next render.

        Returns:
            float: Seconds, 0 if a render is allowed now.
        """
        if self.available() >= 1:
            return 0.0
        return (1 - self.tokens) * 60 / self.per_minute


# --- Producer steps ---


def fill_card_pools(conn: redis.Redis) -> bool:
    """Render one card for the emptiest combination below its pool size.

    Slots only open up when the media Redis starts empty, lost its data or
    the pool size grew; a used card stays pooled until its replacement is in.
    This work ends at the pool size, so it needs no budget.

    Args:
        conn (redis.Redis): Client of the media Redis.

    Returns:
        bool: Whether a card was rendered.
    """
    (fruit, amount), size = min(card_pool_sizes(conn).items(), key=lambda item: item[1])
    if size >= settings.FRUIT_BUZZ_CARD_VARIATIONS_PER_COMBINATION:
        return False
    publish_card(conn, create_card(fruit, amount))
    return True


def replace_card(conn: redis.Redis, budget: RenderBudget) -> bool:
    """Swap the card whose lifetime ended first for a fresh one.

    Without budget the card stays pooled and keeps being served, so heavier
    traffic means more reuse of the same cards, not more rendering.

    Args:
        conn (redis.Redis): Client of the media Redis.
        budget (RenderBudget): Budget of card replacements.

    Returns:
        bool: Whether a card was replaced or, above the pool size, dropped.
    """
    due = conn.zrangebyscore(CARD_RETIRE_KEY, "-inf", time.time(), 0, 1)
    if not due:
        return False
    fruit, amount, template_id = due[0].decode("ascii").split(":")
    amount = int(amount)
    # A lowered pool size shrinks the pool as its cards come due.
    if conn.scard(card_pool_key(fruit, amount)) > (
        settings.FRUIT_BUZZ_CARD_VARIATIONS_PER_COMBINATION
    ):
        publish_card(conn, None, replaces=(fruit, amount, template_id))
        return True
    if budget.available() < 1:
        return False
    budget.spend()
    publish_card(
        conn, create_card(fruit, amount), replaces=(fruit, amount, template_id)
    )
    return True


def produce_captchas(conn: redis.Redis, budget: RenderBudget) -> int:
    """Top the CAPTCHA stock up towards CAPTCHA_POOL_SIZE, one batch at most.

    Each CAPTCHA gets its own random text here, rendered once; the API only
    hands out what is in stock. A stock claimed faster than the budget
    refills runs dry, and the API answers 503 until it recovers.

    Args:
        conn (redis.Redis): Client of the media Redis.
        budget (RenderBudget): Budget of CAPTCHA renders.

    Returns:
        int: CAPTCHAs rendered.
    """
    missing = settings.CAPTCHA_POOL_SIZE - captcha_stock(conn)
    renders = max(0, min(missing, CAPTCHA_BATCH, budget.available()))
    for _ in range(renders):
        text = generate_captcha_string(settings.CAPTCHA_CHAR_LENGTH)
        publish_captcha(conn, text, generate_captcha_image(text))
        budget.spend()
    return renders


# --- Bookkeeping shared by the steps ---


def publish_card(
    conn: redis.Redis,
    card: FruitBuzzCard | None,
    replaces: tuple[str, int, str] | None = None,
) -> None:
    """Pool a rendered card, in place of a used one if given.

    A new card has no TTL and no retire entry. Its first pick schedules its
    replacement (consumer.PICK_CARD_SCRIPT); the replacement starts the old
    template's grace TTL.

    Args:
        conn (redis.Redis): Client of the media Redis.
        card (FruitBuzzCard | None): The rendered card, None to only retire.
        replaces (tuple[str, int, str] | None): Fruit, amount and template ID
            of the card leaving its pool.
    """
    # MULTI stores the image before its ID becomes pickable and swaps both
    # cards at once, so the pool never shrinks or grows in between.
    with conn.pipeline(transaction=True) as pipe:
        if card is not None:
            template_id = uuid.uuid4().hex
            positions = json.dumps(
                [position.model_dump(mode="json") for position in card.fruit_positions],
                separators=(",", ":"),
            )
            pipe.hset(
                card_template_key(template_id),
                mapping={"image": card.image, "positions": positions},
            )
            pipe.sadd(card_pool_key(card.fruit, card.amount), template_id)
        if replaces is not None:
            fruit, amount, old_id = replaces
            pipe.srem(card_pool_key(fruit, amount), old_id)
            pipe.zrem(CARD_RETIRE_KEY, card_retire_member(fruit, amount, old_id))
            # Games that picked it just before still preload it.
            pipe.expire(
                card_template_key(old_id), settings.FRUIT_BUZZ_CARD_GRACE_SECONDS
            )
        pipe.execute()


def publish_captcha(conn: redis.Redis, text: str, image: bytes) -> None:
    """Add one rendered CAPTCHA to the end of the stock, without TTL.

    Args:
        conn (redis.Redis): Client of the media Redis.
        text (str): The CAPTCHA's answer.
        image (bytes): The rendered PNG.
    """
    captcha_id = uuid.uuid4().hex
    with conn.pipeline(transaction=True) as pipe:
        pipe.hset(captcha_key(captcha_id), mapping={"text": text, "image": image})
        pipe.rpush(CAPTCHA_POOL_KEY, captcha_id)
        pipe.execute()


def card_pool_sizes(conn: redis.Redis) -> dict[tuple[str, int], int]:
    """Return the number of pickable cards of every combination.

    Args:
        conn (redis.Redis): Client of the media Redis.

    Returns:
        dict[tuple[str, int], int]: Pool size by (fruit, amount).
    """
    with conn.pipeline(transaction=False) as pipe:
        for fruit, amount in COMBINATIONS:
            pipe.scard(card_pool_key(fruit, amount))
        return dict(zip(COMBINATIONS, pipe.execute()))


def delivered_cards(conn: redis.Redis) -> int:
    """Return the number of pooled cards whose lifetime has started.

    Args:
        conn (redis.Redis): Client of the media Redis.

    Returns:
        int: Cards in the retire set.
    """
    return conn.zcard(CARD_RETIRE_KEY)


def captcha_stock(conn: redis.Redis) -> int:
    """Return the number of ready, unclaimed CAPTCHAs.

    Args:
        conn (redis.Redis): Client of the media Redis.

    Returns:
        int: CAPTCHAs in stock.
    """
    return conn.llen(CAPTCHA_POOL_KEY)


def next_retirement(conn: redis.Redis) -> float | None:
    """Return when the next delivered card's replacement is due.

    Args:
        conn (redis.Redis): Client of the media Redis.

    Returns:
        float | None: Unix time, None if no pooled card was delivered.
    """
    first = conn.zrange(CARD_RETIRE_KEY, 0, 0, withscores=True)
    return first[0][1] if first else None
