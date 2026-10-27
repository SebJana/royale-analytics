from fastapi import APIRouter, HTTPException

from core.deps import DbConn, RedConn
from core.route_timing import note_cache_lookup
from helpers.json_response import get_cached_response, json_response
from mongo import get_game_modes
from redis_service import GAME_MODES_CACHE_KEY

router = APIRouter(prefix="/game_modes", tags=["Game Modes"])


@router.get("", responses={502: {"description": "Game mode lookup failed"}})
async def fetch_game_modes(mongo_conn: DbConn, redis_conn: RedConn):

    try:
        # The cache only holds a copy of Mongo, so a cache outage falls back to
        # Mongo instead of failing the request. The scraper stores exactly the
        # served mapping, so its text is sent as it is.
        cached = await get_cached_response(redis_conn, GAME_MODES_CACHE_KEY)
    except Exception as e:
        print(f"[CACHE] [WARNING] reading the game modes failed, using Mongo: {e}")
        note_cache_lookup(False)
        cached = None
    if cached is not None:
        return cached

    try:
        # The data scraper is the only writer of this key. Caching this Mongo read
        # could overwrite a newer list written after the read and hide a new mode
        # until the TTL ends. Misses are rare (eviction or a redis-cache restart)
        # and the collection holds only a few documents.
        return json_response(await get_game_modes(mongo_conn))

    except Exception as e:
        # Upon a Mongo lookup error
        print(f"[DB] [ERROR] Fetching the game modes failed: {e}")
        raise HTTPException(
            status_code=502, detail="Error trying to fetch the game modes"
        )
