"""Pre-rendered Fruit Buzz cards and CAPTCHAs in the media Redis.

Two sides share the keys below:
- ``producer`` (media-worker only, the single writer): renders cards and
  CAPTCHAs ahead of demand, publishes them and replaces used cards.
- ``consumer`` (the API): picks cards for game rounds and claims CAPTCHAs
  for challenges. It never renders or adds media, it only takes it out.

Guarantees:
- No request renders an image. An empty pool answers with MediaPoolEmpty
  (503 + Retry-After). The producer checks the pools at least every
  MEDIA_WORKER_IDLE_SECONDS; how fast a pool recovers depends on the render
  budget left and on the worker running.
- Media in stock has no TTL; its clock starts on first delivery. A card's
  lifetime starts when a game first picks it, a CAPTCHA's image TTL when it
  is claimed. Without visitors nothing is used up, so the worker sits idle.
- Every fruit/amount combination keeps
  FRUIT_BUZZ_CARD_VARIATIONS_PER_COMBINATION cards. A card whose jittered
  lifetime ended stays pickable until its replacement is rendered, then both
  swap in one step. Its image stays FRUIT_BUZZ_CARD_GRACE_SECONDS longer for
  games that picked it just before.
- Rendering follows traffic up to a cap. Replacements and CAPTCHAs draw from
  per-minute budgets (MEDIA_WORKER_MAX_*_PER_MINUTE). Past them, cards stay in
  service longer and the CAPTCHA stock runs dry (503), so heavier traffic
  gets more reuse or a retry instead of more rendering.
- A CAPTCHA is handed out exactly once: claiming pops it from the stock and
  starts its image TTL in one atomic step.

Keys (all in redis-media):
- ``fruit_buzz:pool:<fruit>:<amount>``: set of template IDs games pick from.
- ``fruit_buzz:template:<id>``: hash with ``image`` (PNG) and ``positions``
  (hit-box JSON); no TTL while pooled, FRUIT_BUZZ_CARD_GRACE_SECONDS once
  replaced.
- ``fruit_buzz:retire``: sorted set ``<fruit>:<amount>:<id>`` of delivered
  cards by the time their lifetime ends.
- ``captcha:pool``: list of ready CAPTCHA IDs, oldest first.
- ``captcha:<id>``: hash with ``text`` and ``image``; no TTL while in stock,
  CAPTCHA_IMAGE_TTL_SECONDS once claimed.

Template IDs never reach the frontend: a game round saves the private ID and
gets its own public image ID; the PNG is encrypted when the client preloads it.
"""

import random

from core.settings import settings

# Key layout: see the module docstring. The Lua scripts in consumer.py take
# the prefixes as arguments, so these are the only spelling of each key.
# Delivered cards by the time their replacement is due.
CARD_RETIRE_KEY = "fruit_buzz:retire"

# Ready CAPTCHA IDs, oldest first.
CAPTCHA_POOL_KEY = "captcha:pool"

# Prefix of the hash holding one CAPTCHA's text and image.
CAPTCHA_KEY_PREFIX = "captcha:"

# Prefix of the hash holding one card's image and hit boxes.
CARD_TEMPLATE_KEY_PREFIX = "fruit_buzz:template:"


class MediaPoolEmpty(Exception):
    """No pre-rendered image is ready; the producer refills the pool.

    Args:
        what (str): The missing media, for the message.
    """

    def __init__(self, what: str):
        super().__init__(f"No pre-rendered {what} is ready")
        self.retry_after = settings.MEDIA_POOL_RETRY_AFTER_SECONDS


def card_pool_key(fruit: str, amount: int) -> str:
    """Return the set key of one fruit/amount combination.

    Args:
        fruit (str): Fruit of the combination.
        amount (int): Fruit count of the combination.

    Returns:
        str: The pool set's key.
    """
    return f"fruit_buzz:pool:{fruit}:{amount}"


def card_template_key(template_id: str) -> str:
    """Return the hash key holding one card's PNG and hit boxes.

    Args:
        template_id (str): Private template ID.

    Returns:
        str: The template hash's key.
    """
    return f"{CARD_TEMPLATE_KEY_PREFIX}{template_id}"


def card_retire_member(fruit: str, amount: int, template_id: str) -> str:
    """Return a card's member in the retire set.

    Args:
        fruit (str): Fruit of the card.
        amount (int): Fruit count of the card.
        template_id (str): Private template ID.

    Returns:
        str: ``<fruit>:<amount>:<id>``.
    """
    return f"{fruit}:{amount}:{template_id}"


def card_lifetime_seconds() -> float:
    """Return how long after first delivery a card's replacement is due.

    The spread keeps replacements, and so the worker's rendering, even
    instead of in bursts when many cards were first picked together.

    Returns:
        float: The mean lifetime in seconds, jittered.
    """
    jitter = settings.FRUIT_BUZZ_CARD_TTL_JITTER
    mean = settings.FRUIT_BUZZ_CARD_TTL_MINUTES * 60
    return mean * random.uniform(1 - jitter, 1 + jitter)


def captcha_key(captcha_id: str) -> str:
    """Return the hash key holding one CAPTCHA's text and PNG.

    Args:
        captcha_id (str): CAPTCHA ID, also the challenge ID.

    Returns:
        str: The CAPTCHA hash's key.
    """
    return f"{CAPTCHA_KEY_PREFIX}{captcha_id}"
