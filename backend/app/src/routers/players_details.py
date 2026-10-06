from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi_limiter.depends import RateLimiter
from typing import Literal, Optional, List
from datetime import datetime

from core.deps import (
    DbConn,
    RedConn,
    TrackedPlayerDep,
    require_tracked_player,
)
from core.settings import settings
from helpers.validate import (
    validate_between_request,
    validate_battles_request,
    validate_game_modes,
    validate_deck_card_filter,
    ParamsRequestError,
)
from models.schema import BetweenRequest, BattlesRequest, DeckCardFilterRequest
from redis_service import get_redis_json, set_redis_json, build_redis_key
from mongo import (
    get_last_battles,
    get_decks_win_percentage,
    get_cards_win_percentage,
    get_daily_stats,
    get_player_profile as get_stored_profile,
)

# TODO: Add consistent rate limits to all player data routes (profile, battles,
# decks, cards, and daily stats), with HTTP 429 and Retry-After for frontend handling.
router = APIRouter(
    prefix="/players",
    tags=["Player Details"],
    dependencies=[Depends(require_tracked_player)],
)


# TODO Count views per player to know which tracked players are viewed
# regularly. This route is the per page view signal: the other player routes
# fire several times per view. Exclude the frontend's polling during a first
# sync. Store the counts in Mongo, not redis-cache: that cache evicts under
# memory pressure. E.g. $inc viewCount and set lastViewedAt on the player
# document, which the scraper already reads for its sync state, without
# delaying the response. The counts could later shorten the sync intervals of
# viewed players and lengthen those of players nobody opens. The explore list
# (TODO above /players/count in players_tracked.py) ranks by views in the
# last 30 days, so keep the counts per day as well; a single running total
# cannot be windowed.
@router.get(
    "/{player_tag}/profile",
    # The profile is a database lookup now, so the limit only guards against
    # abuse. It still allows the frontend's polling during a first sync.
    dependencies=[Depends(RateLimiter(times=60, seconds=60))],
    responses={
        403: {"description": "Invalid or untracked player"},
        404: {"description": "No profile snapshot stored yet"},
        500: {"description": "Player profile lookup failed"},
    },
)
async def get_player_profile(
    player_tag: str, player: TrackedPlayerDep, mongo_conn: DbConn
):
    # Profiles are snapshots the data scraper refreshes daily for active
    # players and up to weekly for idle ones (and the add route stores on
    # tracking). Serving them from Mongo keeps page views from spending Clash
    # Royale API requests.
    try:
        stored = await get_stored_profile(mongo_conn, player_tag)
    except Exception:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch the profile of {player_tag}"
        )

    if not stored or not stored.get("profile"):
        # Only players inserted without the API (e.g. in bulk), until the
        # scraper's first profile refresh for them. The frontend shows a
        # placeholder with the name, so it needs no second request for it.
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PROFILE_NOT_SYNCED",
                "message": f"No profile stored for {player_tag} yet",
                "name": player.name,
            },
        )
    # The sync times tell the frontend how current the shown data is. Battles
    # are checked every few minutes, the profile daily to weekly.
    return {
        **stored["profile"],
        "syncInfo": {
            "battlesSyncedAt": player.last_battles_sync_at,
            "profileSyncedAt": stored.get("syncedAt"),
            "trackedSince": player.tracked_since,
            "trackingGaps": player.tracking_gaps,
        },
    }


@router.get(
    "/{player_tag}/battles",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request parameters"
        },
        404: {"description": "No battles found for the player"},
        500: {"description": "Battle lookup failed"},
    },
)
async def last_battles(
    player_tag: str,
    player: TrackedPlayerDep,
    mongo_conn: DbConn,
    redis_conn: RedConn,
    req: BattlesRequest = Depends(),
):
    try:
        validate_battles_request(req)
        # Either use:
        # 1) the specified before datetime
        # 2) the current datetime, which equals the last req.limit battles, the last N battles
        cutoff = req.before or datetime.now()

        # Without an explicit cutoff the result is "the latest N battles". It
        # only changes with a new sync version, so the key must not contain
        # the current time; otherwise it would never be hit again.
        params = {
            "playerTag": player_tag,
            "before": req.before or "latest",
            "limit": req.limit,
        }
        key = build_redis_key(
            service="crApi",
            resource="playerBattles",
            params=params,
            player_version=player.sync_version,
        )
        cached_battles = await get_redis_json(redis_conn, key)

        if cached_battles is not None:
            return {"player_tag": player_tag, "last_battles": cached_battles}

        battles = await get_last_battles(mongo_conn, player_tag, cutoff, req.limit)

        if not battles:
            raise HTTPException(
                status_code=404, detail=f"No battles found for {player_tag}"
            )

        if player.first_sync_pending and not battles.get("battles"):
            # A just-tracked player whose first battle sync has not finished.
            # Not cached: the next request after the sync must see the battles,
            # and a player without new battles keeps the same sync version.
            return {
                "player_tag": player_tag,
                "last_battles": battles,
                "first_sync_pending": True,
            }

        await set_redis_json(redis_conn, key, battles, ttl=settings.CACHE_TTL_BATTLES)
        return {"player_tag": player_tag, "last_battles": battles}

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch battles for player {player_tag} {e}",
        )


@router.get(
    "/{player_tag}/decks/stats",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request "
            "parameters (dates, unknown or conflicting cards, too many cards)"
        },
        404: {"description": "No decks found for the player"},
        500: {"description": "Deck statistics lookup failed"},
    },
)
async def deck_percentage_stats(
    player_tag: str,
    player: TrackedPlayerDep,
    mongo_conn: DbConn,
    redis_conn: RedConn,
    game_modes: Optional[List[str]] = Query(None),
    card_query: DeckCardFilterRequest = Depends(),
    # Order of the decks before the cap. Match mode ranks by matched cards
    # first. Usage rate orders like battleCount, so it has no own option.
    sort_by: Literal["battleCount", "wins", "winRate", "lastSeen"] = "battleCount",
    sort_order: Literal["asc", "desc"] = "desc",
    # Decks played fewer times are left out, list and totals alike
    min_battles: int = Query(1, ge=1, le=10_000),
    req: BetweenRequest = Depends(),
):
    try:
        validate_between_request(req)
        validated_game_modes = await validate_game_modes(redis_conn, game_modes)
        card_filter = await validate_deck_card_filter(
            mongo_conn,
            redis_conn,
            card_query,
        )
        # TODO add input sanitization for all user-provided parameters
        params = {
            "playerTag": player_tag,
            "startDate": req.start_date,
            "endDate": req.end_date,
            "timezone": req.timezone,
            "gameModes": validated_game_modes,
            "sortBy": sort_by,
            "sortOrder": sort_order,
            "minBattles": min_battles,
        }
        # Without a card filter the key stays the one of all decks
        if card_filter:
            params |= {
                "cardMode": card_filter["mode"],
                "cards": card_filter["cards"],
                "excludeCards": card_filter["exclude_cards"],
                "supportIds": card_filter["support_ids"],
                "excludeSupportIds": card_filter["exclude_support_ids"],
            }
        key = build_redis_key(
            service="crApi",
            resource="playerDecks",
            params=params,
            player_version=player.sync_version,
        )
        cached_decks = await get_redis_json(redis_conn, key)

        if cached_decks is not None:
            return {
                "player_tag": player_tag,
                "game_modes": validated_game_modes,
                "deck_statistics": cached_decks,
            }

        decks = await get_decks_win_percentage(
            mongo_conn,
            player_tag,
            req.start_date,
            req.end_date,
            validated_game_modes,
            req.timezone,
            card_filter,
            sort_by,
            sort_order == "asc",
            settings.DECK_STATS_LIMIT,
            min_battles,
        )

        if not decks:
            raise HTTPException(
                status_code=404, detail=f"No decks found for {player_tag}"
            )

        await set_redis_json(redis_conn, key, decks, ttl=settings.CACHE_TTL_DECK_STATS)
        return {
            "player_tag": player_tag,
            "game_modes": validated_game_modes,
            "deck_statistics": decks,
        }

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch deck statistics for player {player_tag}: {e}",
        )


@router.get(
    "/{player_tag}/cards/stats",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request parameters"
        },
        404: {"description": "No cards found for the player"},
        500: {"description": "Card statistics lookup failed"},
    },
)
async def card_percentage_stats(
    player_tag: str,
    player: TrackedPlayerDep,
    mongo_conn: DbConn,
    redis_conn: RedConn,
    game_modes: Optional[List[str]] = Query(None),
    req: BetweenRequest = Depends(),
):
    try:
        validate_between_request(req)
        validated_game_modes = await validate_game_modes(redis_conn, game_modes)

        params = {
            "playerTag": player_tag,
            "startDate": req.start_date,
            "endDate": req.end_date,
            "timezone": req.timezone,
            "gameModes": validated_game_modes,
        }
        key = build_redis_key(
            service="crApi",
            resource="playerCards",
            params=params,
            player_version=player.sync_version,
        )
        cached_cards = await get_redis_json(redis_conn, key)

        if cached_cards is not None:
            return {
                "player_tag": player_tag,
                "game_modes": validated_game_modes,
                "card_statistics": cached_cards,
            }

        cards = await get_cards_win_percentage(
            mongo_conn,
            player_tag,
            req.start_date,
            req.end_date,
            validated_game_modes,
            req.timezone,
        )

        if not cards:
            raise HTTPException(
                status_code=404, detail=f"No cards found for {player_tag}"
            )

        await set_redis_json(redis_conn, key, cards, ttl=settings.CACHE_TTL_CARD_STATS)
        return {
            "player_tag": player_tag,
            "game_modes": validated_game_modes,
            "card_statistics": cards,
        }

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch the card statistics for player {player_tag}: {e}",
        )


@router.get(
    "/{player_tag}/stats/daily",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request parameters"
        },
        404: {"description": "No daily statistics found for the player"},
        500: {"description": "Daily statistics lookup failed"},
    },
)
async def daily_player_statistics(
    player_tag: str,
    player: TrackedPlayerDep,
    mongo_conn: DbConn,
    redis_conn: RedConn,
    game_modes: Optional[List[str]] = Query(None),
    req: BetweenRequest = Depends(),
):
    try:
        validate_between_request(req)
        validated_game_modes = await validate_game_modes(redis_conn, game_modes)

        params = {
            "playerTag": player_tag,
            "startDate": req.start_date,
            "endDate": req.end_date,
            "timezone": req.timezone,
            "gameModes": validated_game_modes,
        }
        key = build_redis_key(
            service="crApi",
            resource="dailyStats",
            params=params,
            player_version=player.sync_version,
        )
        cached_stats = await get_redis_json(redis_conn, key)

        if cached_stats is not None:
            return {
                "player_tag": player_tag,
                "game_modes": validated_game_modes,
                "daily_statistics": cached_stats,
            }

        stats = await get_daily_stats(
            mongo_conn,
            player_tag,
            req.start_date,
            req.end_date,
            validated_game_modes,
            req.timezone,
        )

        if not stats:
            raise HTTPException(
                status_code=404, detail=f"No battles found for {player_tag}"
            )

        await set_redis_json(
            redis_conn, key, stats, ttl=settings.CACHE_TTL_PLAYER_BATTLE_STATS
        )
        return {
            "player_tag": player_tag,
            "game_modes": validated_game_modes,
            "daily_statistics": stats,
        }

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch the daily statistics for player {player_tag}: {e}",
        )
