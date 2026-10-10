from fastapi import APIRouter, HTTPException

from core.deps import DbConn, RedConn
from core.settings import settings
from core.route_timing import note_cache_lookup
from helpers.json_response import get_cached_response, json_response
from redis_service import CARDS_CACHE_KEY
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
        # Mongo instead of failing the request. The scraper stores exactly the
        # served list, so its text is sent as it is.
        cached = await get_cached_response(redis_conn, CARDS_CACHE_KEY)
    except Exception as e:
        print(f"[CACHE] [WARNING] reading the cards failed, using Mongo: {e}")
        note_cache_lookup(False)
        cached = None
    if cached is not None:
        return cached

    try:
        stored = await get_stored_cards(mongo_conn)

    except Exception as e:
        # A Mongo error does not fall back to the Clash Royale API, so an outage
        # cannot turn every request into a call that spends key quota.
        print(f"[DB] [ERROR] Fetching the cards failed: {e}")
        raise HTTPException(status_code=502, detail="Error trying to fetch the cards")

    # The data scraper is the only writer of the cards in Mongo and the cache
    # (see data_scraper/src/jobs/cards.py). Caching this Mongo read could overwrite
    # a newer list written after the read and keep it until the TTL ends.
    if stored and stored.get("payload"):
        return json_response(stored["payload"])

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
