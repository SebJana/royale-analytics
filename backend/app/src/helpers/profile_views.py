"""Counting profile views without identifying who viewed.

The counts are an indication, not a measurement: they find the most popular
players for the explore list and the badge, and confirm that anyone still
opens a player before it is parked. Undercounting is accepted wherever it
makes faking more expensive. A restart of the views Redis starts a new salt,
so visitors of that day count once more; also accepted.

Two counts come out of every counted view:

- A visit: once per visitor and player within 30 minutes, at most a few a
  day. Shows how often a player is opened.
- A visitor: the first visit of a visitor to a player on a UTC day. Only
  visitors rank the explore list. One address then adds at most one per
  player and day, so faking a ranking needs many addresses for many days.

And a few limits make addresses expensive:

- A visitor is a hash of the client's IP prefix (IPv4 address, IPv6 /64) and
  a salt that changes every UTC day. A client picks its device id or user
  agent freely, but not its IP. The salt lives only in the views Redis; once
  it expires the hashes cannot be traced back to an IP, and Mongo only ever
  receives counts.
- Each network (IPv4 /24, IPv6 /48) adds only a few visitors per player and
  day, so the 254 addresses of one rented range count like a handful. A
  campus or office behind one range is undercounted the same way.
- A view needs a ticket the page fetched at least a few seconds earlier. A
  replayed ticket never counts twice without a marker of its own: it lives
  shorter than the visit its first use opens, and every other rejection
  (caps, budgets, another day's salt) holds for its whole lifetime.
- The ticket only carries a hash of the visitor id and the player, so the
  id the Redis keys use never leaves the server.
- The beacon answers the same whether the view counted or not, so a client
  cannot tell which of its attempts worked.

See docs/profile-views.md for the design, the trade-offs and the privacy notes.
"""

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Request

from core.settings import settings
from helpers.ip_utils import client_ip_prefix, client_network
from helpers.jwt import (
    AvailableTokenTypes,
    create_access_token,
    get_access_token_claims,
)
from mongo import MongoConn, ensure_player_views_indexes
from redis_service import RedisConn

# KEYS[1] open visit, KEYS[2] visits of this visitor to this player today,
# KEYS[3] visits of this visitor today, KEYS[4] visitors of this player from
# this network today.
# ARGV[1] visit TTL, ARGV[2] visits per player cap, ARGV[3] visits per
# visitor budget, ARGV[4] day TTL, ARGV[5] visitors per network cap.
# Returns 0 if the view does not count, 1 for a counted visit of a visitor
# already counted today, 2 for a visitor's first visit today. Atomic, so
# parallel beacons cannot all pass a check before any of them records its
# visit. A rejected view writes nothing, so rejected attempts cannot fill
# the Redis. A network at its cap blocks new visitors only; one already
# counted keeps its visits.
COUNT_VIEW_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
local visits = tonumber(redis.call('GET', KEYS[2]) or '0')
if visits >= tonumber(ARGV[2]) then
    return 0
end
if tonumber(redis.call('GET', KEYS[3]) or '0') >= tonumber(ARGV[3]) then
    return 0
end
local first = visits == 0
if first then
    if tonumber(redis.call('GET', KEYS[4]) or '0') >= tonumber(ARGV[5]) then
        return 0
    end
    redis.call('INCR', KEYS[4])
    redis.call('EXPIRE', KEYS[4], ARGV[4], 'NX')
end
redis.call('SET', KEYS[1], 1, 'EX', ARGV[1])
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], ARGV[4], 'NX')
redis.call('INCR', KEYS[3])
redis.call('EXPIRE', KEYS[3], ARGV[4], 'NX')
if first then
    return 2
end
return 1
"""


@dataclass(frozen=True)
class CountedView:
    """A view that counted.

    day is the UTC day (YYYY-MM-DD) it counts for. new_visitor is True for
    the visitor's first counted visit to the player that day, the only views
    that rank the explore list.
    """

    day: str
    new_visitor: bool


async def create_views_indexes(mongo_conn: MongoConn) -> bool:
    """Create the view count indexes, reporting instead of raising.

    Args:
        mongo_conn (MongoConn): Active MongoDB connection.

    Returns:
        bool: Whether the indexes exist, so views may be counted.
    """

    try:
        await ensure_player_views_indexes(mongo_conn)
        return True
    except Exception as e:
        # Duplicate buckets from before the index make this fail until they
        # are merged; the error names them.
        print(f"[WARNING] Player views indexes missing, views are not counted: {e}")
        return False


async def retry_views_indexes(state, mongo_conn: MongoConn) -> None:
    """Retry the view count indexes until they exist, then enable counting.

    Args:
        state: The app state; views_indexes_ready is set on success.
        mongo_conn (MongoConn): Active MongoDB connection.
    """

    # Started after a failed attempt, so it waits before the next one.
    while True:
        await asyncio.sleep(settings.VIEWS_INDEX_RETRY_S)
        if await create_views_indexes(mongo_conn):
            break
    state.views_indexes_ready = True
    print("[INFO] Player views indexes created, views are counted again")


def view_day() -> str:
    """The current UTC day (YYYY-MM-DD) that salts, caps and counts belong to."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _daily_salt(conn: RedisConn, day: str) -> str:
    """Return the salt of a day, creating it on first use.

    SET NX with GET returns the salt another worker created first, so every
    API worker hashes with the same one.
    """

    created = secrets.token_hex(16)
    existing = await conn.client.set(
        f"views:salt:{day}", created, nx=True, get=True, ex=settings.VIEWS_DAY_TTL
    )
    return existing or created


def _salted_hash(salt: str, value: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:32]


def _ticket_binding(visitor: str, player_tag: str) -> str:
    """What a ticket carries of its visitor: bound to it, but not its id."""

    return _salted_hash(visitor, player_tag)


async def visitor_id(conn: RedisConn, request: Request, day: str) -> str:
    """Hash the client's IP prefix with the day's salt.

    Args:
        conn (RedisConn): The views Redis.
        request (Request): Request of the visitor.
        day (str): UTC day of the salt (see view_day).

    Returns:
        str: The visitor id, equal for one IP prefix within one day only.
    """

    salt = await _daily_salt(conn, day)
    return _salted_hash(salt, client_ip_prefix(request))


async def issue_view_ticket(conn: RedisConn, request: Request, player_tag: str) -> str:
    """Create the ticket a page load needs to count a view.

    Args:
        conn (RedisConn): The views Redis.
        request (Request): Request of the page load.
        player_tag (str): The viewed player's tag, as in the route.

    Returns:
        str: A signed ticket bound to the player and the visitor.

    Raises:
        RedisError: If the salt cannot be read.
    """

    visitor = await visitor_id(conn, request, view_day())
    return create_access_token(
        AvailableTokenTypes.PROFILE_VIEW,
        settings.VIEW_TICKET_EXPIRES_IN,
        {
            "tag": player_tag,
            "visitor": _ticket_binding(visitor, player_tag),
            "iat": int(time.time()),
        },
    )


async def count_view(
    conn: RedisConn, request: Request, player_tag: str, ticket: str
) -> CountedView | None:
    """Count a view if its ticket is valid and the visitor has views left.

    Args:
        conn (RedisConn): The views Redis.
        request (Request): Request of the beacon.
        player_tag (str): The viewed player's tag, as in the route.
        ticket (str): Ticket from issue_view_ticket.

    Returns:
        CountedView | None: The counted view, or None if it does not count.

    Raises:
        RedisError: If the views Redis fails or is full.
    """

    claims = get_access_token_claims(ticket, AvailableTokenTypes.PROFILE_VIEW)
    if claims is None or claims.get("tag") != player_tag:
        return None
    issued_at = claims.get("iat")
    if not isinstance(issued_at, int):
        return None
    if time.time() - issued_at < settings.VIEW_TICKET_MIN_AGE_S:
        return None

    # A ticket from before midnight UTC no longer matches the new day's
    # visitor id; that view is lost, which is rare and harmless.
    day = view_day()
    salt = await _daily_salt(conn, day)
    visitor = _salted_hash(salt, client_ip_prefix(request))
    if claims.get("visitor") != _ticket_binding(visitor, player_tag):
        return None
    # Salted as well, so the key names no network either
    network = _salted_hash(salt, client_network(request))

    counted = await conn.client.eval(
        COUNT_VIEW_SCRIPT,
        4,
        f"views:visit:{player_tag}:{visitor}",
        f"views:cap:{day}:{player_tag}:{visitor}",
        f"views:budget:{day}:{visitor}",
        f"views:network:{day}:{player_tag}:{network}",
        settings.VIEW_VISIT_TTL,
        settings.VIEWS_MAX_PER_IP_PLAYER_DAY,
        settings.VIEWS_MAX_PER_IP_DAY,
        settings.VIEWS_DAY_TTL,
        settings.VIEWS_MAX_VISITORS_PER_NETWORK_PLAYER_DAY,
    )
    if counted == 0:
        return None
    return CountedView(day=day, new_visitor=counted == 2)


def round_view_count(count: int) -> int:
    """Round a view count to two significant digits for display.

    Exact counts would show anyone faking views which attempts landed.

    Args:
        count (int): The exact count.

    Returns:
        int: e.g. 57 stays 57, 1234 becomes 1200.
    """

    if count < 100:
        return count
    magnitude = 10 ** (len(str(count)) - 2)
    return round(count / magnitude) * magnitude
