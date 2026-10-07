from dataclasses import dataclass
from datetime import datetime
from core.settings import settings
from fastapi import Depends, Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Annotated
from helpers.jwt import validate_access_token, AvailableTokenTypes
from redis_service import CacheRedisConn, RedisConn
from clash_royale_api import ClashRoyaleAPI
from mongo import MongoConn, get_tracked_player_cache_state
from scrape_schedule import Schedule
from player_search import PlayerSearchService
from redis.asyncio import Redis


# Dependency that returns the database connection
def get_mongo(request: Request) -> MongoConn:
    db = getattr(request.app.state, "mongo", None)
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return db


# Dependency that returns the redis connection
def get_redis(request: Request) -> CacheRedisConn:
    r = getattr(request.app.state, "redis", None)
    if r is None:
        raise HTTPException(status_code=500, detail="Redis not initialized")
    return r


def get_auth_state_redis(request: Request) -> RedisConn:
    """Return the isolated store for active CAPTCHA and Wordle challenges."""

    r = getattr(request.app.state, "auth_state_redis", None)
    if r is None:
        raise HTTPException(status_code=500, detail="Auth state Redis not initialized")
    return r


def get_card_image_redis(request: Request) -> RedisConn:
    """Return the binary client for the existing Redis cache server.

    The pool size and future preload count come from settings and game rules;
    this connection only reads and writes the associated image bytes.

    Args:
        request (Request): Request whose app owns the Redis connection.

    Returns:
        RedisConn: Connection that returns raw image bytes.
    """
    r = getattr(request.app.state, "card_image_redis", None)
    if r is None:
        raise HTTPException(status_code=500, detail="Card image Redis not initialized")
    return r


# Dependency that returns the Cr API client
def get_cr_api(request: Request) -> ClashRoyaleAPI:
    api = getattr(request.app.state, "cr_api", None)
    if api is None:
        # should not happen if lifespan ran correctly
        raise HTTPException(status_code=500, detail="API client not initialized")
    return api


@dataclass(frozen=True)
class ScrapeSchedules:
    """The data scraper's per-player schedules and the Redis they live in.

    The API only adds and removes players and reads the published capacity;
    the data scraper claims and processes the players.
    """

    battles: Schedule
    profiles: Schedule
    redis: Redis


# Dependency that returns the per-player scraping schedules
def get_scrape_schedules(request: Request) -> ScrapeSchedules:
    schedules = getattr(request.app.state, "scrape_schedules", None)
    if schedules is None:
        raise HTTPException(status_code=500, detail="Scrape schedules not initialized")
    return schedules


def get_player_search(request: Request) -> PlayerSearchService:
    """Return the in-memory player search, which holds every tracked player."""

    search = getattr(request.app.state, "player_search", None)
    if search is None:
        raise HTTPException(status_code=500, detail="Player search not initialized")
    return search


# Global dependencies for usage in the routes
CrApi = Annotated[ClashRoyaleAPI, Depends(get_cr_api)]
DbConn = Annotated[MongoConn, Depends(get_mongo)]
RedConn = Annotated[CacheRedisConn, Depends(get_redis)]
AuthStateConn = Annotated[RedisConn, Depends(get_auth_state_redis)]
CardImageConn = Annotated[RedisConn, Depends(get_card_image_redis)]
Schedules = Annotated[ScrapeSchedules, Depends(get_scrape_schedules)]
PlayerSearch = Annotated[PlayerSearchService, Depends(get_player_search)]


@dataclass(frozen=True)
class TrackedPlayer:
    """A validated, tracked player and the version of its stored battle data.

    sync_version changes whenever the data scraper stores new battles for the
    player. Cache keys built with it are invalidated per player, not globally.
    first_sync_pending is True from tracking until the first battle sync ends.
    last_battles_sync_at is when the scraper last checked the battle log (naive
    UTC), whether or not it found new battles. tracked_since is the date the
    player was first tracked (YYYY-MM-DD), None if unknown. tracking_gaps
    lists untracked periods long enough to have lost battles, as
    {"from", "to"} dates (YYYY-MM-DD) and their length in whole "hours".
    """

    tag: str
    name: str | None
    sync_version: int
    first_sync_pending: bool
    last_battles_sync_at: datetime | None
    tracked_since: str | None
    tracking_gaps: list[dict]


# Dependency that ensures the given player tag is active in the players collection
async def require_tracked_player(
    player_tag: str, cr_api: CrApi, mongo_conn: DbConn
) -> TrackedPlayer:
    """
    FastAPI dependency that ensures a given player tag is valid and currently tracked.

    FastAPI caches the result per request, so a router-level dependency and a
    TrackedPlayerDep route parameter share one Mongo lookup.

    Args:
        player_tag (str): Player tag from the path.
        cr_api (CrApi): Injected Clash Royale API client (for syntax validation).
        mongo_conn (DbConn): Injected Mongo connection (for tracked/active check).

    Returns:
        TrackedPlayer: The player tag and its sync version when validation succeeds.

    Raises:
        HTTPException 403 with a specific code if the tag is invalid or untracked.
    """

    # Check the syntax is valid (takes load off of db and ensures tag is mongo query safe).
    # The syntax check ignores surrounding whitespace, but the routes query and
    # build cache keys with the tag as sent, so only the exact form passes.
    if player_tag != player_tag.strip() or not cr_api.check_tag_syntax(player_tag):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "INVALID_PLAYER_TAG",
                "message": f"Player with tag {player_tag} doesn't exist",
            },
        )

    # Check if the player is in players collection and active
    state = await get_tracked_player_cache_state(mongo_conn, player_tag)
    if state is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLAYER_NOT_TRACKED",
                "message": f"Player with tag {player_tag} isn't being tracked",
            },
        )

    # When its a valid and tracked player, return it with its data version
    return TrackedPlayer(
        tag=player_tag,
        name=state["playerName"],
        sync_version=state["syncVersion"],
        first_sync_pending=state["firstSyncPending"],
        last_battles_sync_at=state["lastBattlesSyncAt"],
        # insertedAt is stored as "YYYY-MM-DD HH-MM-SS"; the date is enough
        tracked_since=str(state["insertedAt"])[:10] if state["insertedAt"] else None,
        tracking_gaps=_relevant_tracking_gaps(state["trackingGaps"]),
    )


def _relevant_tracking_gaps(gaps: list[dict]) -> list[dict]:
    """Keep untracked periods of at least TRACKING_GAP_HINT_MIN_S, as dates.

    Timestamps are stored as "YYYY-MM-DD HH-MM-SS" strings.
    """

    relevant = []
    for gap in gaps:
        try:
            start = datetime.strptime(gap["from"], "%Y-%m-%d %H-%M-%S")
            end = datetime.strptime(gap["to"], "%Y-%m-%d %H-%M-%S")
        except (KeyError, TypeError, ValueError):
            continue
        seconds = (end - start).total_seconds()
        if seconds >= settings.TRACKING_GAP_HINT_MIN_S:
            relevant.append(
                {
                    "from": gap["from"][:10],
                    "to": gap["to"][:10],
                    # A gap within one day is described by its length instead
                    "hours": max(1, round(seconds / 3600)),
                }
            )
    return relevant


TrackedPlayerDep = Annotated[TrackedPlayer, Depends(require_tracked_player)]


# OAuth2 scheme used only for extracting "Authorization: Bearer <token>"
# FastAPI automatically parses the header and provides the raw token.
auth_scheme = HTTPBearer()


# Dependency that ensures authorization token is received and validated
def require_remove_player_token(
    credentials: HTTPAuthorizationCredentials = Depends(auth_scheme),
):
    """
    Validates a Bearer token provided via the Authorization header.
    """
    token = credentials.credentials

    if not validate_access_token(token, AvailableTokenTypes.REMOVE_PLAYER_TOKEN.value):
        raise HTTPException(
            status_code=403,
            detail="No authorization, invalid or expired auth token.",
        )

    return token
