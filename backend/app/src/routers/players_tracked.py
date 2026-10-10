import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi_limiter.depends import RateLimiter
from redis.exceptions import RedisError
from core.deps import (
    AuthStateConn,
    DbConn,
    CrApi,
    PlayerSearch,
    Schedules,
    TrackedPlayerDep,
    require_remove_player_token,
)
from clash_royale_api import (
    ClashRoyaleMaintenanceError,
    ClashRoyalePlayerCheckError,
    ClashRoyaleInvalidTagError,
    ClashRoyalePlayerNotFoundError,
    ClashRoyaleAuthError,
    ClashRoyaleConnectionError,
    ClashRoyaleInvalidResponseError,
)
from api_key_store import NoKeyAvailable, KeyStoreUnavailable
from mongo import (
    insert_tracked_player,
    deactivate_tracked_player,
    get_players_count,
    check_player_tracked,
    save_player_profile,
)
from scrape_schedule import CAPACITY_MAX_AGE_S, read_capacity
from player_search import SearchIndexNotReady
from core.settings import settings
from helpers.token_budget import refund_token

router = APIRouter(prefix="/players", tags=["Tracked Players"])

# TODO Give the actual admin a way to batch insert and untrack player tags
# without the challenge flow, the removal budget and the per-IP rate limits.
# The security questions are a shared, guessable secret, so this must not hang
# off them: use a real secret compared exactly, or keep it off the public API
# (a CLI script against Mongo and the schedules, or a route reachable only
# from the internal network). Batch inserts still spend one Clash Royale
# request per new tag and must respect ensure_tracking_capacity.


# TODO Park the least viewed players when tracking nears capacity, instead of
# refusing new ones. Parked is a third state besides active and deactivated:
# the scraper stops syncing the player (off both schedules), but search still
# lists it and its page shows the stored data with a notice that it is no
# longer tracked. Opening a parked player reactivates it if capacity allows,
# parking the next least viewed player if needed. Rank by the view counts from
# the TODO in players_details.py (views in the last 30 days, then
# lastViewedAt), and spare recently added or viewed players so two of them
# cannot keep parking each other. require_tracked_player has to let parked
# players through, and the state has to stay apart from a user removal
# (e.g. deactivatedReason "parked").
# A protected list (e.g. the top 1000 of the ranked leaderboard) is tracked at
# all times, as the meta analysis needs those players: never parked, and not
# removable through the removal route either, only by the admin. Refresh it
# from the leaderboard regularly; a player leaving the top loses protection
# and is ranked like everyone else. Its size counts against capacity first,
# so the parking only ever shares what is left.
async def ensure_tracking_capacity(
    mongo_conn: DbConn, schedules: Schedules, player_tag: str
):
    """Reject a new player if the data scraper cannot sync one more in time.

    The scraper publishes how many players its keys can keep within the
    battle log window. Already tracked players always pass, and so does every
    player while no recent estimate exists (scraper not running yet).

    Raises:
        HTTPException: 503 with code TRACKING_CAPACITY_REACHED.
    """

    try:
        capacity = await read_capacity(schedules.redis, CAPACITY_MAX_AGE_S)
    except RedisError:
        capacity = None
    if capacity is None or capacity.max_players is None:
        return
    if await check_player_tracked(mongo_conn, player_tag):
        return
    if await get_players_count(mongo_conn) >= capacity.max_players:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TRACKING_CAPACITY_REACHED",
                "message": "The maximum number of tracked players is reached",
            },
        )


# TODO Add a paged list behind an "Explore most popular players" button on the
# home page: all tracked players, most profile views in the last 30 days
# first, with a cursor on (views, playerTag) and a page size limit. Needs the
# view counts from the TODO in players_details.py, kept per day so a 30 day
# window can be summed (e.g. a playerViews collection of {playerTag, day,
# count} with a TTL index on day). Never return every player in one
# response; search covers finding a known player.
@router.get(
    "/count",
    responses={500: {"description": "Tracked player count lookup failed"}},
)
async def fetch_tracked_player_count(mongo_conn: DbConn):
    try:
        players_count = await get_players_count(mongo_conn)
        return {"activePlayerCount": players_count}
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to fetch the count of all tracked players"
        )


# NOTE Keep this above any future GET "/{player_tag}" route, which would
# otherwise capture "/search" as a player tag.
@router.get(
    "/search",
    # Autocomplete with a ~150 ms debounce sends a few requests per second
    # while typing; this still stops scripted enumeration of all players.
    dependencies=[Depends(RateLimiter(times=120, seconds=60))],
    responses={
        422: {"description": "Query missing or too long"},
        429: {"description": "Too many searches from this client"},
        503: {"description": "Search index is still being built"},
    },
)
async def search_tracked_players(
    search: PlayerSearch,
    q: Annotated[str, Query(max_length=settings.SEARCH_QUERY_MAX_LENGTH)],
):
    """Search tracked players by name or tag, best match first.

    Names and tags are both searched and a complete tag is pinned first. A
    leading "#" ranks every tag match above every name match. Matches at the
    start of a
    name rank above matches at a word start, which rank above matches
    anywhere else. Queries shorter than three characters only match the
    start of a name or tag, except Chinese, Japanese and Korean ones,
    where one character is already a word. hasMore tells the frontend that matches beyond
    the returned ones exist, so it can ask for a more specific query.
    """

    limit = settings.SEARCH_RESULT_LIMIT
    try:
        # One extra result reveals whether more matches exist.
        results = search.search(q, limit + 1)
    except SearchIndexNotReady as e:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SEARCH_INDEX_NOT_READY",
                "message": "Player search is starting up",
            },
            headers={"Retry-After": str(settings.SEARCH_BUILD_RETRY_S)},
        ) from e
    return {
        "players": [
            {"tag": r.tag, "name": r.name, "match": r.match} for r in results[:limit]
        ],
        "hasMore": len(results) > limit,
    }


# NOTE The app key pool is the hard limit on how fast players can be added,
# independent of the per-client limit below. Every add spends one Clash
# Royale request (the profile check), and this is the API's only Clash Royale
# call. At most CR_API_APP_KEY count x CR_KEY_REQUESTS_PER_SECOND adds start
# per second across all clients: with 1 key at 1 req/s, 60 per minute. Misuse
# that spreads over many IPs to get past the per-client limit still cannot
# exceed it; the surplus waits for a key and then gets 503 with Retry-After.
# Adding app keys raises this ceiling. Total tracked players stay bounded by
# the scraper's capacity check (TRACKING_CAPACITY_REACHED).
@router.post(
    "/{player_tag}",
    dependencies=[Depends(RateLimiter(times=3, seconds=60))],
    responses={
        404: {"description": "Player tag invalid or player not found"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
        500: {"description": "Could not save tracked player"},
        502: {"description": "Clash Royale API request failed"},
        503: {
            "description": "Clash Royale API or key store unavailable, "
            "or the scraper has no capacity for another player"
        },
    },
)
async def add_tracked_player(
    player_tag: str,
    mongo_conn: DbConn,
    cr_api: CrApi,
    schedules: Schedules,
    search: PlayerSearch,
):
    # Use the same trimmed tag for the Clash Royale check and the stored player.
    player_tag = player_tag.strip()

    # Checked before the Clash Royale request, so a full scraper does not
    # spend app key requests on players it would reject anyway.
    await ensure_tracking_capacity(mongo_conn, schedules, player_tag)

    try:
        profile = await cr_api.check_existing_player(player_tag)
    # Keep missing players and Clash Royale failures separate for the frontend.
    except ClashRoyaleMaintenanceError as e:
        raise HTTPException(
            status_code=e.code,
            detail={"code": "CR_API_MAINTENANCE", "message": e.detail},
        ) from e
    except NoKeyAvailable as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "CR_API_KEYS_BUSY", "message": str(e)},
            headers={"Retry-After": str(int(e.retry_after))},
        ) from e
    except KeyStoreUnavailable as e:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CR_API_KEY_STORE_UNAVAILABLE",
                "message": "Clash Royale key store unavailable",
            },
        ) from e
    except ClashRoyaleInvalidTagError as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "INVALID_PLAYER_TAG", "message": e.detail},
        ) from e
    except ClashRoyalePlayerNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "PLAYER_NOT_FOUND", "message": e.detail},
        ) from e
    except ClashRoyaleAuthError as e:
        raise HTTPException(
            status_code=502,
            detail={"code": "CR_API_AUTH_FAILED", "message": e.detail},
        ) from e
    except ClashRoyaleConnectionError as e:
        raise HTTPException(
            status_code=502,
            detail={"code": "CR_API_UNAVAILABLE", "message": e.detail},
        ) from e
    except ClashRoyaleInvalidResponseError as e:
        raise HTTPException(
            status_code=502,
            detail={"code": "CR_API_INVALID_RESPONSE", "message": e.detail},
        ) from e
    except ClashRoyalePlayerCheckError as e:
        # Other Clash Royale failures still need to stay separate from backend errors.
        raise HTTPException(
            status_code=502,
            detail={"code": "CR_API_UNAVAILABLE", "message": e.detail},
        ) from e

    try:
        status_insert = await insert_tracked_player(
            mongo_conn, player_tag, profile["name"]
        )
        # The verification response is a complete profile. Storing it as the
        # first snapshot makes the profile page work right away, without
        # another Clash Royale request.
        await save_player_profile(mongo_conn, player_tag, profile)
    except Exception:
        raise HTTPException(
            status_code=500, detail=f"Player {player_tag} could not be tracked"
        )

    # Searchable before the response goes out. Already tracked players are
    # upserted too: the profile was just fetched, so it repairs a stale name.
    search.upsert(player_tag, profile["name"])

    if status_insert in ("created", "reactivated"):
        # Due time 0 puts the player in front of the battle schedule, so its
        # first battle sync starts within seconds instead of after a full
        # interval. The profile was just stored, so its refresh is due after
        # the normal period. Mongo is written first: the scraper's reconciler
        # relies on that order.
        try:
            await schedules.battles.add(player_tag, due_ms=0)
            await schedules.profiles.add(
                player_tag,
                due_ms=int((time.time() + settings.PROFILE_MIN_INTERVAL) * 1000),
            )
        except RedisError as e:
            # The reconciler schedules the player within a few minutes anyway.
            print(f"[WARNING] Could not schedule {player_tag} immediately: {e}")

    if status_insert == "reactivated":
        return {"status": "Player is now being tracked again", "tag": player_tag}
    if status_insert == "created":
        return {"status": "Player is now being tracked", "tag": player_tag}
    if status_insert == "already_tracked":
        return {"status": "Player is already being tracked", "tag": player_tag}

    return {"status": "Player is being tracked", "tag": player_tag}


@router.delete(
    "/{player_tag}",
    dependencies=[Depends(RateLimiter(times=3, seconds=60))],
    responses={
        403: {
            "description": (
                "Invalid or untracked player, removal token invalid or expired "
                "(REMOVE_PLAYER_TOKEN_EXPIRED), or its removals used up "
                "(REMOVE_PLAYER_TOKEN_USED_UP)"
            )
        },
        404: {"description": "Tracked player not found"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
        500: {"description": "Could not remove tracked player"},
    },
)
async def remove_tracked_player(
    mongo_conn: DbConn,
    schedules: Schedules,
    search: PlayerSearch,
    auth_state_conn: AuthStateConn,
    player: TrackedPlayerDep,
    # After the player check, so an invalid or untracked tag costs no removal.
    token_claims: Annotated[dict, Depends(require_remove_player_token)],
):
    player_tag = player.tag
    try:
        affected_player_count = await deactivate_tracked_player(mongo_conn, player_tag)

        # Database operation returns 0 if no matching records were modified
        # Tracked player is verified with every given player tag, but handle async untrack issues
        # with this catch here
        if affected_player_count == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Player with tag {player_tag} is not being tracked",
            )

        search.remove(player_tag)

        try:
            await schedules.battles.remove(player_tag)
            await schedules.profiles.remove(player_tag)
        except RedisError as e:
            # The scraper drops inactive players on their next claim or the
            # next reconciliation, so this is only a delay.
            print(f"[WARNING] Could not unschedule {player_tag}: {e}")

        return {"status": "Player is not being tracked anymore", "tag": player_tag}

    except HTTPException:
        raise  # keep original FastAPI errors
    except Exception:
        # A failure that removed nobody is no spent removal. Mongo can commit
        # the deactivation and still fail the request (a lost acknowledgement),
        # so refund only when the player is verifiably still tracked. A 404
        # above keeps the charge: the request did name a player, just one
        # already removed.
        try:
            unchanged = await check_player_tracked(mongo_conn, player_tag)
        except Exception:
            unchanged = False
        if unchanged:
            await refund_token(auth_state_conn, token_claims)
        raise HTTPException(
            status_code=500,
            detail=f"Player {player_tag} could not be removed from tracking",
        )
