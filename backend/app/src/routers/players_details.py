from fastapi import APIRouter, HTTPException, Depends, Query, Request, Response
from fastapi_limiter.depends import RateLimiter
from typing import Literal, Optional, List
from datetime import datetime, timezone
from redis.exceptions import RedisError

from core.deps import (
    DbConn,
    RedConn,
    TrackedPlayer,
    TrackedPlayerDep,
    ViewsConn,
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
from models.schema import (
    BetweenRequest,
    BattlesRequest,
    DeckCardFilterRequest,
    ProfileViewRequest,
)
from helpers.profile_views import count_view, issue_view_ticket, round_view_count
from helpers.json_response import (
    cache_json_response,
    get_cached_response,
    json_response,
)
from redis_service import build_redis_key
from mongo import (
    count_battles_in_ranges,
    get_last_battles,
    get_decks_win_percentage,
    get_cards_win_percentage,
    get_daily_stats,
    get_player_profile as get_stored_profile,
    record_profile_view,
)

# TODO: Add consistent rate limits to all player data routes (profile, battles,
# decks, cards, and daily stats), with HTTP 429 and Retry-After for frontend handling.

router = APIRouter(
    prefix="/players",
    tags=["Player Details"],
    dependencies=[Depends(require_tracked_player)],
)


def _tracking_state(player: TrackedPlayer, pause_battles: int | None) -> dict:
    """Tracking state of the profile response.

    since is the day tracking was paused (YYYY-MM-DD), passiveBattles the
    battles other tracked players stored since then. Both are None while the
    player is tracked, or if the pause start is unknown.
    """

    if not player.parked:
        return {"state": "active"}
    return {
        "state": "parked",
        "since": (
            player.parked_since.date().isoformat() if player.parked_since else None
        ),
        "passiveBattles": pause_battles,
    }


# TODO The view counts (view routes below) could shorten the sync intervals
# of viewed players and lengthen those of players nobody opens.
@router.get(
    "/{player_tag}/profile",
    # The profile is a database lookup now, so the limit only guards against
    # abuse. It still allows the frontend's polling during a first sync.
    dependencies=[Depends(RateLimiter(times=60, seconds=60))],
    responses={
        403: {"description": "Invalid or untracked player"},
        404: {"description": "No profile snapshot stored yet"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
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
        # Battles are stored for every participant, so a player has some on
        # record from before it was tracked, or from a tracking gap, that
        # came from other tracked players. The frontend notes them.
        ranges = [player.passive_range] + [
            gap["passiveRange"] for gap in player.tracking_gaps
        ]
        # The current pause is an untracked period as well, still open
        pause = (player.parked_since, None) if player.parked_since else None
        ranges.append(pause)
        known = [r for r in ranges if r is not None]
        counts = iter(await count_battles_in_ranges(mongo_conn, player_tag, known))
        # None where the period was recorded before its own battles were
        passive = [next(counts) if r is not None else None for r in ranges]
        pause_battles = passive.pop()
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
                # A parked player gets no profile until it is tracked again,
                # so the frontend offers that instead of waiting.
                "tracking": _tracking_state(player, pause_battles),
            },
        )
    # Only the top of the explore list gets a badge, with a rounded count:
    # exact counts would show anyone faking views which attempts landed.
    popularity = None
    if (
        player.explore_rank is not None
        and player.explore_rank <= settings.POPULAR_TOP_X
    ):
        popularity = {
            "rank": player.explore_rank,
            "views30d": round_view_count(player.views_30d),
        }
    # The sync times tell the frontend how current the shown data is. Battles
    # are checked every few minutes, the profile daily to weekly.
    return {
        **stored["profile"],
        "popularity": popularity,
        # A parked player's data stops at the time it was parked. The frontend
        # says so, lists the pause with the untracked periods, and offers to
        # track it again.
        "tracking": _tracking_state(player, pause_battles),
        "syncInfo": {
            "battlesSyncedAt": player.last_battles_sync_at,
            # Rises with every sync that stores battles of the player. The
            # frontend refetches its cached battles and statistics computed
            # at an older version (their syncVersion field).
            "syncVersion": player.sync_version,
            "profileSyncedAt": stored.get("syncedAt"),
            "trackedSince": player.tracked_since,
            # Noted by the frontend under "Tracked since"
            "battlesBeforeTracking": passive[0],
            "trackingGaps": [
                {
                    **{k: v for k, v in gap.items() if k != "passiveRange"},
                    "passiveBattles": count,
                }
                for gap, count in zip(player.tracking_gaps, passive[1:])
            ],
        },
    }


# Polled by an open player page to notice new battles (useSyncVersionRefresh
# in the frontend). The router's tracked check already read the version, so
# this costs one lookup and no query of its own, where the profile would also
# count battles.
@router.get(
    "/{player_tag}/sync-version",
    dependencies=[Depends(RateLimiter(times=30, seconds=60))],
    responses={
        403: {"description": "Invalid or untracked player"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_sync_version(player: TrackedPlayerDep):
    return json_response({"syncVersion": player.sync_version})


# One ticket per page load (useProfileView in the frontend). Not part of the
# profile response, which the browser caches: a cached ticket would be spent
# or expired on the next visit.
@router.get(
    "/{player_tag}/view-ticket",
    dependencies=[Depends(RateLimiter(times=30, seconds=60))],
    responses={
        403: {"description": "Invalid or untracked player"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
        503: {"description": "View counting unavailable"},
    },
)
async def get_view_ticket(
    player_tag: str, request: Request, response: Response, views_conn: ViewsConn
):
    try:
        ticket = await issue_view_ticket(views_conn, request, player_tag)
    except RedisError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "VIEWS_UNAVAILABLE",
                "message": "View counting is unavailable",
            },
        ) from e
    response.headers["Cache-Control"] = "no-store"
    return {"ticket": ticket}


# Always 204 for a tracked player, whether the view counted or not: a client
# faking views cannot tell which attempts worked. Nothing here logs the
# client address or visitor id with the tag (docs/profile-views.md).
@router.post(
    "/{player_tag}/view",
    status_code=204,
    dependencies=[Depends(RateLimiter(times=30, seconds=60))],
    responses={
        403: {"description": "Invalid or untracked player"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def record_view(
    player_tag: str,
    body: ProfileViewRequest,
    request: Request,
    views_conn: ViewsConn,
    mongo_conn: DbConn,
):
    # Without the unique index, two first views of a day could create two
    # buckets and the index could never be built (see
    # ensure_player_views_indexes). Checked before Redis, so no visitor's
    # allowance is spent.
    if not getattr(request.app.state, "views_indexes_ready", False):
        return Response(status_code=204)
    try:
        counted = await count_view(views_conn, request, player_tag, body.ticket)
    except RedisError as e:
        # A full views Redis (noeviction) only stops counting
        print(f"[WARNING] Profile view not counted, views Redis failed: {e}")
        return Response(status_code=204)
    if counted is None:
        return Response(status_code=204)
    try:
        await record_profile_view(
            mongo_conn, player_tag, counted.day, counted.new_visitor
        )
    except Exception:
        # Logged by the helper; a lost view is not worth an error page
        pass
    return Response(status_code=204)


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
        # Naive UTC like the stored battleTime; datetime.now() would be the
        # host's local time, hours off outside a UTC container.
        cutoff = req.before or datetime.now(timezone.utc).replace(tzinfo=None)

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
            resource="playerBattlesResponse",
            params=params,
            player_version=player.sync_version,
        )
        # A player parked before its first sync gets cached answers without
        # the pending flag. Tracked again, it keeps its sync version, so those
        # answers would stop the frontend from waiting for the first sync.
        if not player.first_sync_pending:
            cached = await get_cached_response(redis_conn, key)
            if cached is not None:
                return cached

        battles = await get_last_battles(mongo_conn, player_tag, cutoff, req.limit)

        if not battles:
            raise HTTPException(
                status_code=404, detail=f"No battles found for {player_tag}"
            )

        if player.first_sync_pending:
            # A just-tracked player whose first battle sync has not finished.
            # Also with battles: other players' syncs may have stored older
            # ones, and the flag keeps the frontend polling until the sync adds
            # the latest. Not cached: the next request after the sync must see
            # them, and a player without new battles keeps the same sync version.
            return json_response(
                {
                    "player_tag": player_tag,
                    "last_battles": battles,
                    "first_sync_pending": True,
                    "includes_newest_battle": False,
                    "syncVersion": player.sync_version,
                }
            )

        # Whether the page starts with the newest stored battle, so a streak
        # running into it is still going, as of the last sync. Without a
        # cutoff the page is the latest battles by definition. With one, the
        # watermark of the player document stands in for a lookup of newer
        # battles. It only moves with new battles, which also bump the sync
        # version, so the cached flag cannot go stale under its key.
        # NOTE Battles stored by another player's sync can be newer than the
        # watermark. A cutoff that leaves out only those still reads as newest
        # until the player's own sync, a few minutes at most.
        latest = battles.get("latestBattleTime")
        includes_newest = req.before is None or (
            latest is not None
            and player.last_battle_time is not None
            and latest >= player.last_battle_time
        )

        return await cache_json_response(
            redis_conn,
            key,
            {
                "player_tag": player_tag,
                "last_battles": battles,
                "includes_newest_battle": includes_newest,
                # The version the cache key holds, so the frontend can tell a
                # stale copy of its own (see the profile's syncInfo)
                "syncVersion": player.sync_version,
            },
            ttl=settings.CACHE_TTL_BATTLES,
        )

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        # The exception text can name hosts and queries, so it stays in the log
        print(f"[ERROR] Fetching the battles of {player_tag} failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch battles for player {player_tag}",
        )


@router.get(
    "/{player_tag}/decks/stats",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request "
            "parameters (dates or season, unknown or conflicting cards, too many "
            "cards, invalid game modes or both game_modes and exclude_game_modes)"
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
    # Modes to leave out instead, for a selection of nearly every mode
    exclude_game_modes: Optional[List[str]] = Query(None),
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
        start, end = validate_between_request(req)
        validated_game_modes, excluded_game_modes = await validate_game_modes(
            redis_conn, game_modes, exclude_game_modes
        )
        card_filter = await validate_deck_card_filter(
            mongo_conn,
            redis_conn,
            card_query,
        )
        # Keyed by the resolved UTC window, not by how it was requested. The
        # timezone only placed the dates, which the window already holds.
        params = {
            "playerTag": player_tag,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "gameModes": validated_game_modes,
            "sortBy": sort_by,
            "sortOrder": sort_order,
            "minBattles": min_battles,
        }
        # Without excluded modes the key stays the one of the include filter
        if excluded_game_modes:
            params["excludeGameModes"] = excluded_game_modes
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
            resource="playerDecksResponse",
            params=params,
            player_version=player.sync_version,
        )
        cached = await get_cached_response(redis_conn, key)
        if cached is not None:
            return cached

        decks = await get_decks_win_percentage(
            mongo_conn,
            player_tag,
            start,
            end,
            validated_game_modes,
            card_filter,
            sort_by,
            sort_order == "asc",
            settings.DECK_STATS_LIMIT,
            min_battles,
            excluded_game_modes,
        )

        if not decks:
            raise HTTPException(
                status_code=404, detail=f"No decks found for {player_tag}"
            )

        return await cache_json_response(
            redis_conn,
            key,
            {
                "player_tag": player_tag,
                "game_modes": validated_game_modes,
                "exclude_game_modes": excluded_game_modes,
                "deck_statistics": decks,
                "syncVersion": player.sync_version,
            },
            ttl=settings.CACHE_TTL_DECK_STATS,
        )

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        print(f"[ERROR] Fetching the deck statistics of {player_tag} failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch deck statistics for player {player_tag}",
        )


@router.get(
    "/{player_tag}/cards/stats",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request "
            "parameters (dates or season, invalid game modes or both game_modes "
            "and exclude_game_modes)"
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
    # Modes to leave out instead, for a selection of nearly every mode
    exclude_game_modes: Optional[List[str]] = Query(None),
    req: BetweenRequest = Depends(),
):
    try:
        start, end = validate_between_request(req)
        validated_game_modes, excluded_game_modes = await validate_game_modes(
            redis_conn, game_modes, exclude_game_modes
        )

        params = {
            "playerTag": player_tag,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "gameModes": validated_game_modes,
        }
        if excluded_game_modes:
            params["excludeGameModes"] = excluded_game_modes
        key = build_redis_key(
            service="crApi",
            resource="playerCardsResponse",
            params=params,
            player_version=player.sync_version,
        )
        cached = await get_cached_response(redis_conn, key)
        if cached is not None:
            return cached

        cards = await get_cards_win_percentage(
            mongo_conn,
            player_tag,
            start,
            end,
            validated_game_modes,
            excluded_game_modes,
        )

        if not cards:
            raise HTTPException(
                status_code=404, detail=f"No cards found for {player_tag}"
            )

        return await cache_json_response(
            redis_conn,
            key,
            {
                "player_tag": player_tag,
                "game_modes": validated_game_modes,
                "exclude_game_modes": excluded_game_modes,
                "card_statistics": cards,
                "syncVersion": player.sync_version,
            },
            ttl=settings.CACHE_TTL_CARD_STATS,
        )

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        print(f"[ERROR] Fetching the card statistics of {player_tag} failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch the card statistics for player {player_tag}",
        )


@router.get(
    "/{player_tag}/stats/daily",
    responses={
        403: {
            "description": "Invalid or untracked player, or invalid request "
            "parameters (dates or season, invalid game modes or both game_modes "
            "and exclude_game_modes)"
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
    # Modes to leave out instead, for a selection of nearly every mode
    exclude_game_modes: Optional[List[str]] = Query(None),
    req: BetweenRequest = Depends(),
):
    try:
        start, end = validate_between_request(req)
        validated_game_modes, excluded_game_modes = await validate_game_modes(
            redis_conn, game_modes, exclude_game_modes
        )

        # The timezone groups the days, so it stays part of the key
        params = {
            "playerTag": player_tag,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": req.timezone,
            "gameModes": validated_game_modes,
        }
        if excluded_game_modes:
            params["excludeGameModes"] = excluded_game_modes
        key = build_redis_key(
            service="crApi",
            resource="dailyStatsResponse",
            params=params,
            player_version=player.sync_version,
        )
        cached = await get_cached_response(redis_conn, key)
        if cached is not None:
            return cached

        stats = await get_daily_stats(
            mongo_conn,
            player_tag,
            start,
            end,
            validated_game_modes,
            req.timezone,
            excluded_game_modes,
        )

        if not stats:
            raise HTTPException(
                status_code=404, detail=f"No battles found for {player_tag}"
            )

        return await cache_json_response(
            redis_conn,
            key,
            {
                "player_tag": player_tag,
                "game_modes": validated_game_modes,
                "exclude_game_modes": excluded_game_modes,
                "daily_statistics": stats,
                "syncVersion": player.sync_version,
            },
            ttl=settings.CACHE_TTL_PLAYER_BATTLE_STATS,
        )

    except ParamsRequestError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)

    except HTTPException:
        raise

    except Exception as e:
        print(f"[ERROR] Fetching the daily statistics of {player_tag} failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch the daily statistics for player {player_tag}",
        )
