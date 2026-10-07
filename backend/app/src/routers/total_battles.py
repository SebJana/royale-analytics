from fastapi import APIRouter, HTTPException

from core.deps import DbConn, RedConn
from core.settings import settings
from mongo import get_battles_count
from redis_service import get_redis_json, set_redis_json, build_redis_key

router = APIRouter(prefix="/battles", tags=["Total battles"])


@router.get(
    "/total_count",
    responses={502: {"description": "Total battle count lookup failed"}},
)
async def fetch_battles_count(mongo_conn: DbConn, redis_conn: RedConn):
    try:
        key = build_redis_key(service="crApi", resource="totalBattles")
        cached_battle_count = await get_redis_json(redis_conn, key)

        if cached_battle_count is not None:
            return {"totalBattleCount": cached_battle_count}
        battle_count = await get_battles_count(mongo_conn)
        await set_redis_json(
            redis_conn, key, battle_count, ttl=settings.CACHE_TTL_TOTAL_BATTLES
        )
        return {"totalBattleCount": battle_count}

    except Exception as e:
        # Upon any lookup/redis error
        print(f"[ERROR] Fetching the total battle count failed: {e}")
        raise HTTPException(
            status_code=502, detail="Error trying to fetch the total battle count"
        )
