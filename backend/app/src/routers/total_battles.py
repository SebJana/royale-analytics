from fastapi import APIRouter, HTTPException

from core.deps import DbConn, RedConn
from core.settings import settings
from helpers.json_response import cache_json_response, get_cached_response
from mongo import get_battles_count
from redis_service import build_redis_key

router = APIRouter(prefix="/battles", tags=["Total battles"])


@router.get(
    "/total_count",
    responses={502: {"description": "Total battle count lookup failed"}},
)
async def fetch_battles_count(mongo_conn: DbConn, redis_conn: RedConn):
    try:
        key = build_redis_key(service="crApi", resource="totalBattlesResponse")
        cached = await get_cached_response(redis_conn, key)
        if cached is not None:
            return cached
        battle_count = await get_battles_count(mongo_conn)
        return await cache_json_response(
            redis_conn,
            key,
            {"totalBattleCount": battle_count},
            ttl=settings.CACHE_TTL_TOTAL_BATTLES,
        )

    except Exception as e:
        # Upon any lookup/redis error
        print(f"[ERROR] Fetching the total battle count failed: {e}")
        raise HTTPException(
            status_code=502, detail="Error trying to fetch the total battle count"
        )
