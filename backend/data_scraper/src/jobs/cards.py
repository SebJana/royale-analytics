"""Periodic refresh of the Clash Royale card list.

Cards change rarely and do not belong to any player, so they are refreshed on
their own timer instead of with the battle syncs. Mongo keeps the durable copy
and Redis the fast one. This job is the only writer of both: the API reads Redis,
then Mongo, never caches what it read, and never calls the Clash Royale API for
cards. A single writer keeps an older list from overwriting a newer one.

Each refresh also mirrors the card art into a self-hosted image set (see
card_images.py). The stored list points to that set only once it is complete.
"""

import asyncio
import logging
from datetime import datetime, timezone

from card_images import (
    attach_image_urls,
    build_image_set,
    load_image_set,
    prune_image_sets,
)
from clash_royale_api import ClashRoyaleAPI
from mongo import MongoConn, get_cards, save_cards
from redis_service import CacheRedisConn, CARDS_CACHE_KEY, set_redis_json
from settings import settings

logger = logging.getLogger(__name__)


def is_valid_card_list(cards) -> bool:
    """Check the card list response before it replaces the stored one.

    Args:
        cards: Response of the Clash Royale /cards endpoint.

    Returns:
        bool: True for {"items": [...]} with at least one card that has an id
            and a name. The frontend reads exactly these fields.
    """

    if not isinstance(cards, dict):
        return False
    items = cards.get("items")
    if not isinstance(items, list) or not items:
        return False
    return all(
        isinstance(card, dict) and card.get("id") is not None and card.get("name")
        for card in items
    )


async def refresh_cards(
    cr_api: ClashRoyaleAPI,
    mongo_conn: MongoConn,
    redis_conn: CacheRedisConn,
    stored: dict | None,
):
    """Fetch the card list, mirror its images, and store it in Mongo and the cache.

    A failed image set does not hold back the card list: the list keeps
    pointing to the previous set where its art is unchanged, the rest falls
    back to the CDN, and the stored imagesComplete flag makes the next refresh
    come after CARDS_RETRY_DELAY instead of the full interval. Images the CDN
    does not deliver yet are left out of the set and counted in imagesMissing,
    which brings the next refresh forward to CARD_IMAGE_MISSING_RETRY_DELAY.
    On a fresh install the list is stored once before the images, with CDN
    URLs only, so the API does not wait for the first image build.

    Args:
        cr_api (ClashRoyaleAPI): Client for the card list request.
        mongo_conn (MongoConn): Durable copy of the card list.
        redis_conn (CacheRedisConn): Cache the API reads first.
        stored (dict | None): The stored card document (see get_cards).

    Raises:
        ValueError: If the response is not a usable card list. The stored list
            stays in place.
        Exception: Any API, Mongo, or Redis error; the caller retries later.
    """

    cards = await cr_api.get_cards()
    # A successful but empty or error-shaped response would otherwise replace
    # the last good list and count as fresh for a full refresh interval.
    if not is_valid_card_list(cards):
        raise ValueError("Card list response has an unexpected shape")

    if not stored or not stored.get("payload"):
        # Until a list is stored the API answers /cards with 503. Mirroring
        # every image first could take minutes on a slow CDN, so a fresh
        # install serves the list with its CDN URLs right away.
        await store_cards(
            mongo_conn,
            redis_conn,
            cards,
            image_version=None,
            images_complete=False,
            images_missing=0,
        )

    previous_version = stored.get("imageVersion") if stored else None
    try:
        image_set = await build_image_set(cards, previous_version)
        images_complete = True
        images_missing = len(image_set.missing)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Card image set failed, keeping the previous one")
        image_set = await asyncio.to_thread(
            load_image_set, previous_version, current_format=False
        )
        images_complete = False
        images_missing = 0

    image_version = image_set.version if image_set else None
    await store_cards(
        mongo_conn,
        redis_conn,
        attach_image_urls(cards, image_set),
        image_version,
        images_complete,
        images_missing,
    )
    logger.info("Cards refreshed in Mongo and cache")

    if image_version:
        # Only after both copies point to image_version, so no reader can
        # still be handed a set that is about to go.
        try:
            await asyncio.to_thread(prune_image_sets, image_version)
        except Exception:
            logger.exception("Removing old card image sets failed")


async def store_cards(
    mongo_conn: MongoConn,
    redis_conn: CacheRedisConn,
    served: dict,
    image_version: str | None,
    images_complete: bool,
    images_missing: int,
):
    """Write the served card list to Mongo, then to the cache the API reads.

    Args:
        mongo_conn (MongoConn): Durable copy of the card list.
        redis_conn (CacheRedisConn): Cache the API reads first.
        served (dict): Card list as the API serves it.
        image_version (str | None): See save_cards.
        images_complete (bool): See save_cards.
        images_missing (int): See save_cards.
    """

    await save_cards(mongo_conn, served, image_version, images_complete, images_missing)
    await set_redis_json(
        conn=redis_conn, key=CARDS_CACHE_KEY, value=served, ttl=settings.CACHE_TTL_CARDS
    )


def seconds_until_cards_due(stored: dict | None) -> float:
    """Time until the stored card list is older than the refresh interval.

    A restart therefore does not trigger an extra card request when the stored
    list is still recent. A list stored without a complete image set is due
    again after CARDS_RETRY_DELAY, which also builds the first set for a list
    stored before images were mirrored. A list with images the CDN does not
    deliver yet is due after CARD_IMAGE_MISSING_RETRY_DELAY.

    Args:
        stored (dict | None): The stored card document (see get_cards).

    Returns:
        float: Seconds until the next refresh, 0 if it is due now.
    """

    if not stored or not stored.get("updatedAt"):
        return 0
    # Motor returns naive datetimes that are UTC
    updated_at = stored["updatedAt"].replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if not stored.get("imagesComplete"):
        return max(0.0, settings.CARDS_RETRY_DELAY - age)
    if stored.get("imagesMissing"):
        return max(0.0, settings.CARD_IMAGE_MISSING_RETRY_DELAY - age)
    return max(0.0, settings.CARDS_REFRESH_INTERVAL - age)


async def image_set_lost(stored: dict | None) -> bool:
    """Check whether the set the stored card list points to is gone from disk.

    Happens when the card-images volume is deleted while Mongo keeps the card
    list (e.g. `docker compose down -v`), or when a file or the manifest of the
    set is damaged. Image URLs of the list then return 404, and browsers fall
    back to the CDN, until a refresh rebuilds the set.

    Args:
        stored (dict | None): The stored card document (see get_cards).

    Returns:
        bool: True if the list points to a set that does not exist.
    """

    version = stored.get("imageVersion") if stored else None
    if not version:
        return False
    return not await asyncio.to_thread(load_image_set, version, current_format=False)


async def restore_cards_cache(stored: dict | None, redis_conn: CacheRedisConn):
    """Copy the stored card list into the cache if the cache lost it.

    The cache can lose the list to eviction, a redis-cache restart, or a write
    that failed after the Mongo save. Rebuilding it from Mongo costs no Clash
    Royale request, so it does not wait for the next refresh.

    Args:
        stored (dict | None): The stored card document (see get_cards).
        redis_conn (CacheRedisConn): Connection to the cache Redis the API reads.
    """

    if not stored or not stored.get("payload"):
        return
    if await redis_conn.client.exists(CARDS_CACHE_KEY):
        return
    await set_redis_json(
        conn=redis_conn,
        key=CARDS_CACHE_KEY,
        value=stored["payload"],
        ttl=settings.CACHE_TTL_CARDS,
    )
    logger.info("Card cache restored from Mongo")


async def cards_loop(
    cr_api: ClashRoyaleAPI,
    mongo_conn: MongoConn,
    redis_conn: CacheRedisConn,
):
    """Refresh the card list every CARDS_REFRESH_INTERVAL until cancelled.

    Between refreshes the loop wakes every CARDS_CACHE_CHECK_INTERVAL to
    restore a lost cache entry from Mongo and to rebuild a lost image set.
    """

    while True:
        try:
            stored = await get_cards(mongo_conn)
            delay = seconds_until_cards_due(stored)
            if delay > 0 and await image_set_lost(stored):
                logger.warning("Card image set is gone from disk, rebuilding it")
                delay = 0
            if delay <= 0:
                await refresh_cards(cr_api, mongo_conn, redis_conn, stored)
                continue
            await restore_cards_cache(stored, redis_conn)
            await asyncio.sleep(min(delay, settings.CARDS_CACHE_CHECK_INTERVAL))
        except asyncio.CancelledError:
            raise
        except Exception:
            # The API keeps serving the previous cards from Redis or Mongo.
            logger.exception("Card refresh failed, retrying later")
            await asyncio.sleep(settings.CARDS_RETRY_DELAY)
