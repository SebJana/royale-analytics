from fastapi import APIRouter, HTTPException

from core.deps import DbConn, RedConn
from core.settings import settings
from redis_service import get_redis_json, CARDS_CACHE_KEY
from mongo import get_cards as get_stored_cards

router = APIRouter(prefix="/cards", tags=["Cards"])


@router.get(
    "",
    responses={
        502: {"description": "Card lookup failed"},
        503: {"description": "Cards not stored yet (CARDS_NOT_READY)"},
    },
)
async def get_cards(mongo_conn: DbConn, redis_conn: RedConn):
    try:
        # The cache only holds a copy of Mongo, so a cache outage falls back to
        # Mongo instead of failing the request.
        cached_cards = await get_redis_json(redis_conn, CARDS_CACHE_KEY)
    except Exception as e:
        print(f"[CACHE] [WARNING] reading the cards failed, using Mongo: {e}")
        cached_cards = None
    if cached_cards is not None:
        return cached_cards

    try:
        stored = await get_stored_cards(mongo_conn)

    except Exception as e:
        # A Mongo error does not fall back to the Clash Royale API, so an outage
        # cannot turn every request into a call that spends key quota.
        raise HTTPException(
            status_code=502, detail=f"Error trying to fetch the cards: {e}"
        )

    # The data scraper is the only writer of the cards in Mongo and the cache
    # (see data_scraper/src/jobs/cards.py). Caching this Mongo read could overwrite
    # a newer list written after the read and keep it until the TTL ends.
    if stored and stored.get("payload"):
        return stored["payload"]

    # Only on a fresh install, before the scraper's first card refresh. The
    # scraper stores the list before mirroring its images, so this resolves
    # within seconds of its first Clash Royale request.
    raise HTTPException(
        status_code=503,
        detail={
            "code": "CARDS_NOT_READY",
            "message": "Cards are not available yet, try again shortly",
        },
        headers={"Retry-After": str(settings.CARDS_NOT_READY_RETRY_AFTER)},
    )
