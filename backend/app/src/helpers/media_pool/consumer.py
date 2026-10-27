"""API side of the media pools: picks cards and claims CAPTCHAs.

It never renders or adds media. Its writes start the clock on delivered
media: a first pick schedules the card's replacement, claiming pops a CAPTCHA
and starts its TTL. A picked card ID whose template is gone (a wiped media
Redis) is removed from its pool. Its Redis user needs those writes too.
"""

import json
import time

from core.settings import settings
from helpers.fruit_buzz_card import AVAILABLE_FRUITS, FRUIT_POSITIONS
from helpers.fruit_buzz_rendering.models import FruitBuzzCard
from redis_service import RedisConn

from . import (
    CAPTCHA_KEY_PREFIX,
    CAPTCHA_POOL_KEY,
    CARD_RETIRE_KEY,
    CARD_TEMPLATE_KEY_PREFIX,
    MediaPoolEmpty,
    captcha_key,
    card_lifetime_seconds,
    card_pool_key,
    card_retire_member,
    card_template_key,
)

# KEYS[1] is the CAPTCHA ID list, ARGV[1] the CAPTCHA key prefix and ARGV[2]
# the image TTL in seconds. Popping and starting the TTL in one step means
# a claimed CAPTCHA can neither go to a second request nor stay forever if the
# API stops right after the pop.
CLAIM_CAPTCHA_SCRIPT = """
local id = redis.call('LPOP', KEYS[1])
if not id then
    return false
end
local key = ARGV[1] .. id
redis.call('EXPIRE', key, ARGV[2])
return {id, redis.call('HGET', key, 'text')}
"""

# KEYS[1] is the combination's pool set, KEYS[2] the retire set. ARGV[1] is
# the template key prefix, ARGV[2] the retire member prefix
# ("<fruit>:<amount>:") and ARGV[3] when this card's lifetime would end. Only
# the first pick adds the card to the retire set (NX), so its lifetime counts
# from its first delivery and later picks do not extend it. In one step with
# the pick, the producer cannot replace the card in between and have the
# retire entry come back for a card no longer pooled. Returns false for an
# empty pool, {id} for a pooled ID without template, else {id, image,
# positions}.
PICK_CARD_SCRIPT = """
local id = redis.call('SRANDMEMBER', KEYS[1])
if not id then
    return false
end
local fields = redis.call('HMGET', ARGV[1] .. id, 'image', 'positions')
if not fields[1] or not fields[2] then
    redis.call('SREM', KEYS[1], id)
    return {id}
end
redis.call('ZADD', KEYS[2], 'NX', ARGV[3], ARGV[2] .. id)
return {id, fields[1], fields[2]}
"""


async def get_card_template(
    conn: RedisConn, template_id: str, fruit: str, amount: int
) -> FruitBuzzCard | None:
    """Load the raw PNG and hit boxes saved for one game round.

    Args:
        conn (RedisConn): Binary connection to the media Redis.
        template_id (str): Private template ID saved with the round.
        fruit (str): Fruit of that round.
        amount (int): Fruit count of that round.

    Returns:
        FruitBuzzCard | None: The card, or None once its grace has ended.
    """
    fields = await conn.client.hgetall(card_template_key(template_id))
    if b"image" not in fields or b"positions" not in fields:
        return None
    return FruitBuzzCard(
        image=fields[b"image"],
        fruit=fruit,
        amount=amount,
        fruit_positions=json.loads(fields[b"positions"]),
    )


async def pick_pool_card(
    conn: RedisConn, fruit: str, amount: int
) -> tuple[str, FruitBuzzCard]:
    """Pick a random ready card of one fruit/amount combination.

    The first pick of a card starts its lifetime, see PICK_CARD_SCRIPT.

    Args:
        conn (RedisConn): Binary connection to the media Redis.
        fruit (str): Fruit selected for the round.
        amount (int): Fruit count selected for the round.

    Returns:
        tuple[str, FruitBuzzCard]: Private template ID and its card.

    Raises:
        ValueError: For an unsupported combination.
        MediaPoolEmpty: If the combination has no ready card.
    """
    if fruit not in AVAILABLE_FRUITS or amount not in FRUIT_POSITIONS:
        raise ValueError("Unsupported Fruit Buzz card combination.")
    # A pooled ID outlives its image only if the media Redis lost data (a
    # restart). The script drops such IDs; a few tries find a live one.
    for _ in range(3):
        picked = await conn.client.eval(
            PICK_CARD_SCRIPT,
            2,
            card_pool_key(fruit, amount),
            CARD_RETIRE_KEY,
            CARD_TEMPLATE_KEY_PREFIX,
            card_retire_member(fruit, amount, ""),
            time.time() + card_lifetime_seconds(),
        )
        if not picked:
            break
        if len(picked) == 3:
            template_id, image, positions = picked
            return template_id.decode("ascii"), FruitBuzzCard(
                image=image,
                fruit=fruit,
                amount=amount,
                fruit_positions=json.loads(positions),
            )
    raise MediaPoolEmpty(f"{fruit} x{amount} card")


async def claim_captcha(conn: RedisConn) -> tuple[str, str]:
    """Take one ready CAPTCHA for a new challenge.

    Args:
        conn (RedisConn): Binary connection to the media Redis.

    Returns:
        tuple[str, str]: The CAPTCHA ID, which is also the challenge ID, and
            its text.

    Raises:
        MediaPoolEmpty: If the stock is empty.
    """
    claimed = await conn.client.eval(
        CLAIM_CAPTCHA_SCRIPT,
        1,
        CAPTCHA_POOL_KEY,
        CAPTCHA_KEY_PREFIX,
        settings.CAPTCHA_IMAGE_TTL_SECONDS,
    )
    if not claimed or claimed[1] is None:
        raise MediaPoolEmpty("CAPTCHA")
    return claimed[0].decode("ascii"), claimed[1].decode("utf-8")


async def get_captcha_image(conn: RedisConn, captcha_id: str) -> bytes | None:
    """Return a claimed CAPTCHA's PNG.

    Args:
        conn (RedisConn): Binary connection to the media Redis.
        captcha_id (str): ID returned by ``claim_captcha``.

    Returns:
        bytes | None: The PNG, None once its image TTL ended.
    """
    return await conn.client.hget(captcha_key(captcha_id), "image")
