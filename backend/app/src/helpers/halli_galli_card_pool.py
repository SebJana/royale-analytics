"""Reuse rendered Halli Galli cards without exposing reusable IDs to players.

The set ``halli_galli:pool:banana:4`` contains IDs for banana cards with four
fruits. Each ID points to a ``halli_galli:template:<id>`` hash holding the PNG
and its fruit hit boxes. The PNG and boxes share one key so Redis evicts them
together. The pool grows to the configured limit, then reuses random templates.
Each template expires after the configured TTL, even if it is used often, so
expired variations are replaced as the game continues.

Only these server-side templates are reused. A game round saves the private
template ID and gets its own public image ID. The PNG is encrypted when the
client asks to preload that round; template IDs never go to the frontend.
Redis can still evict these keys earlier under cache pressure. If an image is
missing, its stale ID is removed from the index or the round gets a replacement.
"""

import asyncio
import json
import uuid

from core.settings import settings
from helpers.halli_galli_rendering.models import HalliGalliCard
from helpers.halli_galli_card import (
    AVAILABLE_FRUITS,
    FRUIT_POSITIONS,
    create_card,
)
from redis_service import RedisConn

# Redis EXPIRE takes seconds, while the game setting is kept in minutes.
SECONDS_PER_MINUTE = 60


# KEYS[1] is the fruit/amount set and KEYS[2] is the new template hash.
# ARGV[1] is the pool limit, ARGV[2] the private template ID, ARGV[3] the PNG,
# ARGV[4] the JSON fruit positions, and ARGV[5] the TTL in seconds. The set is
# how a combination is found; the template hash holds its image and hit
# boxes by ID. Both keys receive expiry when the template is published.
# The template's expiry is never refreshed by a later pool lookup. The index
# expiry moves forward only when a new variation is added to that pool.
# This check and both writes run atomically in Redis. Concurrent requests may
# render at the same time, but cannot store more templates than the limit.
PUBLISH_CARD_SCRIPT = """
-- Another request may have filled the pool while this card was rendered.
if redis.call('SCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
-- Store the complete template before making its ID available for selection.
redis.call('HSET', KEYS[2], 'image', ARGV[3], 'positions', ARGV[4])
redis.call('SADD', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


def _pool_key(fruit: str, amount: int) -> str:
    """Build the index key for one fruit and amount combination.

    Args:
        fruit (str): Fruit drawn on the card.
        amount (int): Number of fruits drawn on the card.

    Returns:
        str: Redis set key containing this combination's template IDs.
    """
    return f"halli_galli:pool:{fruit}:{amount}"


def _template_key(template_id: str) -> str:
    """Build the Redis key holding one reusable image and its hit boxes.

    Args:
        template_id (str): Private ID for the rendered variation.

    Returns:
        str: Redis hash key for the variation.
    """
    return f"halli_galli:template:{template_id}"


async def _load_cached_card(
    conn: RedisConn, template_id: bytes, fruit: str, amount: int
) -> HalliGalliCard | None:
    """Load one template's PNG and fruit boxes from its Redis hash.

    Args:
        conn (RedisConn): Binary Redis connection for the cache.
        template_id (bytes): Private ID selected from a fruit/amount set.
        fruit (str): Fruit requested by the game.
        amount (int): Fruit count requested by the game.

    Returns:
        HalliGalliCard | None: Stored card, or None if its hash was evicted.
    """
    fields = await conn.client.hgetall(_template_key(template_id.decode("ascii")))
    if b"image" not in fields or b"positions" not in fields:
        return None
    # The connection uses decode_responses=False. PNG bytes remain raw, while
    # json.loads decodes the separately stored hit-box JSON.
    return HalliGalliCard(
        image=fields[b"image"],
        fruit=fruit,
        amount=amount,
        fruit_positions=json.loads(fields[b"positions"]),
    )


async def _pick_cached_card(
    conn: RedisConn, fruit: str, amount: int
) -> tuple[str, HalliGalliCard] | None:
    """Choose a random template and remove stale IDs left by cache eviction.

    Args:
        conn (RedisConn): Binary Redis connection for the cache.
        fruit (str): Fruit requested by the game.
        amount (int): Fruit count requested by the game.

    Returns:
        tuple[str, HalliGalliCard] | None: Private ID and cached card, or None.
    """
    pool_key = _pool_key(fruit, amount)
    # Check at most the current set size. Each failed lookup removes one stale
    # ID, so this loop finishes even if the cache evicted every template hash.
    remaining = await conn.client.scard(pool_key)
    while remaining:
        template_id = await conn.client.srandmember(pool_key)
        if template_id is None:
            break
        card = await _load_cached_card(conn, template_id, fruit, amount)
        if card is not None:
            return template_id.decode("ascii"), card
        # Redis can evict the template hash while the set still names it.
        # Remove that ID so the set counts only available templates.
        await conn.client.srem(pool_key, template_id)
        remaining -= 1
    return None


async def _render_card(fruit: str, amount: int) -> HalliGalliCard:
    """Render one new variation without blocking the API event loop.

    Args:
        fruit (str): Fruit to draw on the card.
        amount (int): Number of fruits to draw.

    Returns:
        HalliGalliCard: Newly rendered PNG with its fruit hit boxes.
    """
    card = await asyncio.to_thread(create_card, fruit, amount)
    if card is None:
        raise ValueError("Card generation failed for a valid combination.")
    return card


async def _store_card_if_room(
    conn: RedisConn, card: HalliGalliCard, max_variations: int
) -> str | None:
    """Store a new template if its fruit/amount pool is still below the limit.

    Args:
        conn (RedisConn): Binary Redis connection for the cache.
        card (HalliGalliCard): Newly rendered PNG and hit boxes to store.
        max_variations (int): Maximum templates allowed in this combination.

    Returns:
        str | None: Its new private template ID, or None if the pool was full.
    """
    ttl_minutes = settings.HALLI_GALLI_CARD_TTL_MINUTES
    if ttl_minutes < 1:
        raise ValueError("The card variation TTL must be at least one minute.")

    template_id = uuid.uuid4().hex

    # Keep the PNG binary and encode only the small hit-box metadata as JSON.
    # Both fields live in one hash so a cached template is either complete or
    # missing after Redis evicts it.
    positions = json.dumps(
        [position.model_dump(mode="json") for position in card.fruit_positions],
        separators=(",", ":"),
    ).encode("utf-8")
    added = await conn.client.eval(
        PUBLISH_CARD_SCRIPT,
        2,
        _pool_key(card.fruit, card.amount),
        _template_key(template_id),
        max_variations,
        template_id,
        card.image,
        positions,
        ttl_minutes * SECONDS_PER_MINUTE,
    )
    return template_id if added else None


async def get_card_template(
    conn: RedisConn, template_id: str, fruit: str, amount: int
) -> HalliGalliCard | None:
    """Load the raw PNG selected for one game round.

    Args:
        conn (RedisConn): Binary connection to the cache Redis.
        template_id (str): Private template ID saved with the game round.
        fruit (str): Fruit selected for that round.
        amount (int): Fruit count selected for that round.

    Returns:
        HalliGalliCard | None: Raw PNG and hit boxes, or None if evicted.
    """
    return await _load_cached_card(conn, template_id.encode("ascii"), fruit, amount)


async def get_or_create_card(
    conn: RedisConn, fruit: str, amount: int
) -> tuple[str, HalliGalliCard]:
    """Grow a combination's pool to its limit, then reuse a random card.

    Rendering runs in a thread so it does not block the API event loop. The
    Redis publish script enforces the limit from settings even when multiple
    requests see an underfilled pool at the same time.

    Args:
        conn (RedisConn): Binary connection to the evictable cache Redis.
        fruit (str): Fruit selected for the game round.
        amount (int): Number of fruits selected for the game round.

    Returns:
        tuple[str, HalliGalliCard]: Private template ID and its raw card.
    """
    if fruit not in AVAILABLE_FRUITS or amount not in FRUIT_POSITIONS:
        raise ValueError("Unsupported Halli Galli card combination.")
    max_variations = settings.HALLI_GALLI_CARD_VARIATIONS_PER_COMBINATION
    if max_variations < 1:
        raise ValueError("The card variation limit must be positive.")

    # A full pool can serve an existing card without doing any image rendering.
    pool_key = _pool_key(fruit, amount)
    if await conn.client.scard(pool_key) >= max_variations:
        cached = await _pick_cached_card(conn, fruit, amount)
        # The picker may have removed stale IDs whose template hashes were
        # evicted. Refill the pool when that drops it below the desired size.
        if cached is not None and await conn.client.scard(pool_key) >= max_variations:
            return cached

    # An underfilled pool needs one new variation. Publishing still checks the
    # limit atomically because another request may have filled it meanwhile.
    card = await _render_card(fruit, amount)
    template_id = await _store_card_if_room(conn, card, max_variations)
    if template_id is not None:
        return template_id, card

    # Another worker filled the pool while this card was being rendered. Use
    # one of its stored cards instead of growing beyond the configured limit.
    # If Redis evicted the pool at the same time, retry publishing the fresh
    # card so the game always has a stored raw template to refer to.
    cached = await _pick_cached_card(conn, fruit, amount)
    if cached is not None:
        return cached
    template_id = await _store_card_if_room(conn, card, max_variations)
    if template_id is None:
        raise ValueError("Could not store or find a Halli Galli card template.")
    return template_id, card
