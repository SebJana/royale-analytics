import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi_limiter.depends import RateLimiter
from redis.exceptions import LockError, RedisError
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
    remove_parked_player,
    get_players_count,
    check_player_tracked,
    get_tracked_player_cache_state,
    get_park_candidates,
    PARKED_REASON,
    get_explore_page,
    save_player_profile,
)
from scrape_schedule import read_capacity, schedule_settings, spare_since
from player_search import SearchIndexNotReady
from core.settings import settings
from helpers.token_budget import refund_token
from helpers.profile_views import round_view_count

router = APIRouter(prefix="/players", tags=["Tracked Players"])

# Redis lock (scheduling Redis) around every add that parks a player
ADMISSION_LOCK_KEY = "crsched:admission"

# TODO Give the actual admin a way to batch insert and untrack player tags
# without the challenge flow, the removal budget and the per-IP rate limits.
# The security questions are a shared, guessable secret, so this must not hang
# off them: use a real secret compared exactly, or keep it off the public API
# (a CLI script against Mongo and the schedules, or a route reachable only
# from the internal network). Batch inserts still spend one Clash Royale
# request per new tag and must respect ensure_tracking_capacity.


# TODO Track the top TOP_PLAYERS_N of the last finished season, protected
# from parking and from the removal route, with TOP_PLAYERS_N slots reserved
# in the admission count so a full new list always fits. The season
# finish of the ranked leaderboard comes from
# /v1/locations/global/pathoflegend/{seasonId}/rankings/players?limit=N
# (seasonId as YYYY-MM, e.g. 2026-09). Page through it with the
# paging.cursors.after cursor up to the top N.
async def ensure_tracking_capacity(
    mongo_conn: DbConn, schedules: Schedules, player_tag: str
) -> int | None:
    """Check whether the data scraper can sync one more player in time.

    The scraper publishes how many players its keys can keep within the
    battle log window. Already tracked players always pass, and so does every
    player while no recent estimate exists (scraper not running yet). A full
    pool admits the player as long as another one can be parked for it
    (admit_at_capacity).

    Two adds at the same time can both pass at one below the limit. The
    scraper's hourly parking check brings usage back below its watermarks.

    Returns:
        int | None: The admission limit if the pool is full, so the player
        has to go through admit_at_capacity, otherwise None.

    Raises:
        HTTPException: 503 with code TRACKING_CAPACITY_REACHED if the pool is
            full and no player can be parked.
    """

    try:
        capacity = await read_capacity(
            schedules.redis, schedule_settings.CAPACITY_MAX_AGE_S
        )
    except RedisError:
        capacity = None
    if capacity is None or capacity.max_players is None:
        return None
    if await check_player_tracked(mongo_conn, player_tag):
        return None
    count = await get_players_count(mongo_conn)
    if count < capacity.max_players:
        return None
    # Every active player can be parked, so a full pool has someone to park
    # unless the limit is 0. Checked before the Clash Royale request, so such
    # a pool does not spend app key requests on players it rejects anyway.
    if count == 0:
        raise _capacity_reached()
    return capacity.max_players


async def admit_at_capacity(
    mongo_conn: DbConn,
    schedules: Schedules,
    player_tag: str,
    player_name: str,
    max_players: int,
) -> str:
    """Park one player and add player_tag, as one step for all full-pool adds.

    The lock serializes this step across requests: without it, two adds of
    the same player would each park someone for a single new player. Under
    the lock the player and the count are checked again, as the Clash Royale
    request before took a while. The lock expires on its own if the process
    dies while holding it.

    Args:
        mongo_conn (DbConn): Mongo connection.
        schedules (Schedules): Scraping schedules; their Redis holds the lock.
        player_tag (str): The player to add.
        player_name (str): Its name from the Clash Royale check.
        max_players (int): The admission limit ensure_tracking_capacity saw.

    Returns:
        str: The result of insert_tracked_player.

    Raises:
        HTTPException: 503 TRACKING_BUSY if other full-pool adds hold the
            lock too long, 503 TRACKING_CAPACITY_REACHED if nobody can be
            parked, 500 if the player could not be stored.
    """

    lock = schedules.redis.lock(
        ADMISSION_LOCK_KEY,
        timeout=settings.ADMISSION_LOCK_TTL_S,
        blocking_timeout=settings.ADMISSION_LOCK_WAIT_S,
    )
    try:
        acquired = await lock.acquire()
    except RedisError:
        acquired = False
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TRACKING_BUSY",
                "message": "Too many players are being added at once",
            },
            headers={"Retry-After": str(int(settings.ADMISSION_LOCK_WAIT_S))},
        )
    try:
        parked = None
        if (
            not await check_player_tracked(mongo_conn, player_tag)
            and await get_players_count(mongo_conn) >= max_players
        ):
            parked = await make_room(mongo_conn, schedules, player_tag)
        try:
            return await insert_tracked_player(mongo_conn, player_tag, player_name)
        except Exception:
            if parked:
                await _unpark(mongo_conn, parked)
            raise HTTPException(
                status_code=500, detail=f"Player {player_tag} could not be tracked"
            )
    finally:
        try:
            await lock.release()
        except (LockError, RedisError):
            # Expired already: another add may have run meanwhile, which only
            # brings back the rare double parking the lock is there to stop.
            pass


async def make_room(mongo_conn: DbConn, schedules: Schedules, player_tag: str) -> str:
    """Park the first player in parking order to make room for player_tag.

    A candidate can be untracked by a removal between the read and the
    write, so the next ones are tried before giving up.

    Returns:
        str: The tag of the parked player.

    Raises:
        HTTPException: 503 with code TRACKING_CAPACITY_REACHED if none of the
            candidates could be parked.
    """

    candidates = await get_park_candidates(
        mongo_conn, settings.PARK_ON_DEMAND_CANDIDATES, spare_since()
    )
    for tag in candidates:
        if not await deactivate_tracked_player(mongo_conn, tag, PARKED_REASON):
            continue
        print(f"[INFO] Parked {tag} to make room for {player_tag}")
        # The search keeps listing a parked player, so only the schedules
        # change.
        try:
            await schedules.battles.remove(tag)
            await schedules.profiles.remove(tag)
        except RedisError as e:
            # The scraper drops inactive players on their next claim or the
            # next reconciliation, so this is only a delay.
            print(f"[WARNING] Could not unschedule parked {tag}: {e}")
        return tag
    raise _capacity_reached()


async def _unpark(mongo_conn: DbConn, player_tag: str):
    """Track a player again that was parked for an add that then failed.

    The reconciler puts it back on the schedules within a few minutes. Its
    pause of a few milliseconds stays below the untracked periods the
    profile lists.
    """

    try:
        await insert_tracked_player(mongo_conn, player_tag)
        print(f"[INFO] Tracked {player_tag} again after a failed add")
    except Exception as e:
        print(f"[ERROR] {player_tag} stays parked after a failed add: {e}")


def _capacity_reached() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "TRACKING_CAPACITY_REACHED",
            "message": "The maximum number of tracked players is reached",
        },
    )


# The player search on the home page shows it as its Popular list.
# NOTE Keep this above any future GET "/{player_tag}" route, like /search.
@router.get(
    "/explore",
    dependencies=[Depends(RateLimiter(times=60, seconds=60))],
    responses={
        422: {"description": "Cursor or limit out of range"},
        429: {"description": "Too many requests from this client"},
        500: {"description": "Explore list lookup failed"},
    },
)
async def explore_players(
    mongo_conn: DbConn,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[
        int, Query(ge=1, le=settings.EXPLORE_PAGE_SIZE_MAX)
    ] = settings.EXPLORE_PAGE_SIZE,
):
    """Most viewed tracked players of the last 30 days, most viewed first.

    The data scraper ranks the players every few hours (exploreRank). The
    cursor is the last rank of the previous page, and counts are rounded, so
    exact view counts never leave the API. Never returns every player in one
    response; search covers finding a known player.
    """

    try:
        # One extra player reveals whether another page exists.
        players = await get_explore_page(mongo_conn, cursor, limit + 1)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Failed to fetch the explore list"
        ) from e
    page = players[:limit]
    return {
        "players": [
            {
                "tag": p["playerTag"],
                "name": p.get("playerName"),
                "rank": p["exploreRank"],
                "views30d": round_view_count(p.get("views30d", 0)),
            }
            for p in page
        ],
        "nextCursor": page[-1]["exploreRank"] if len(players) > limit else None,
    }


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
            "or the scraper is full and no player can be parked, or too many "
            "players are being added at once (TRACKING_BUSY, Retry-After)"
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
    full_at = await ensure_tracking_capacity(mongo_conn, schedules, player_tag)

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

    # Only after the Clash Royale check, so a mistyped tag parks nobody.
    if full_at is not None:
        status_insert = await admit_at_capacity(
            mongo_conn, schedules, player_tag, profile["name"], full_at
        )
    else:
        try:
            status_insert = await insert_tracked_player(
                mongo_conn, player_tag, profile["name"]
            )
        except Exception:
            raise HTTPException(
                status_code=500, detail=f"Player {player_tag} could not be tracked"
            )

    try:
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
        # A parked player is already off the schedules; removing it only
        # drops it from the search.
        if player.parked:
            affected_player_count = await remove_parked_player(mongo_conn, player_tag)
        else:
            affected_player_count = await deactivate_tracked_player(
                mongo_conn, player_tag
            )

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
        # so refund only when the player is verifiably still tracked or
        # parked. A 404 above keeps the charge: the request did name a player,
        # just one already removed.
        try:
            unchanged = (
                await get_tracked_player_cache_state(mongo_conn, player_tag) is not None
            )
        except Exception:
            unchanged = False
        if unchanged:
            await refund_token(auth_state_conn, token_claims)
        raise HTTPException(
            status_code=500,
            detail=f"Player {player_tag} could not be removed from tracking",
        )
