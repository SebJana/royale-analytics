import redis.asyncio as redis
import hashlib
import json
from urllib.parse import quote
from datetime import date, datetime, time
import random

# The data scraper is the only writer of the card list (see jobs/cards.py) and
# refreshes it on a timer, so the key has no version. The API only reads it.
CARDS_CACHE_KEY = "crApi:allCards"

# The data scraper is the only writer of the game mode list (see game_modes_loop)
# and rewrites it as soon as a new mode is stored. The API only reads it.
GAME_MODES_CACHE_KEY = "crApi:allGameModes"


class RedisConn:
    """Wrapper for an async Redis connection.

    Used directly for state whose key must remain stable for its entire TTL,
    such as an in-progress authentication challenge.

    Args:
        host (str): Redis service hostname or IP address.
        port (int): Redis service port.
        password (str): Password required by the Redis service.
        decode_responses (bool): Decode Redis string responses as ``str``.
    """

    def __init__(
        self, host: str, port: int, password: str, decode_responses: bool = True
    ):
        self._host = host
        self._port = port
        self._password = password
        self._decode = decode_responses
        self.client: redis.Redis | None = None

    async def connect(self):
        """
        Establish an async connection to Redis and perform a ping to fail if unreachable.
        """

        self.client = redis.Redis(
            host=self._host,
            port=self._port,
            password=self._password,
            decode_responses=self._decode,
            socket_connect_timeout=3,
            socket_timeout=5,
        )
        # Perform health check to confirm connection works
        await self.client.ping()  # fail if connection couldn't be established

    async def close(self):
        """Close the Redis client if it was initialized."""

        if self.client is not None:
            await self.client.aclose()


class CacheRedisConn(RedisConn):
    """Redis connection for reconstructible data (redis-cache).

    Entries may be evicted at any time. Player statistics are invalidated
    through the player's syncVersion in their key (see ``build_redis_key``);
    everything else expires through its TTL.
    """


async def get_redis_json(conn: RedisConn, key: str):
    """
    Fetch a JSON value from Redis and deserialize it.

    Args:
        conn (RedisConn): Redis connection that owns ``key``.
        key (str): Redis key to fetch.

    Returns:
        The deserialized Python object if found, otherwise None.
    """

    raw_data = await conn.client.get(key)
    return json.loads(raw_data) if raw_data else None


def _json_default(object):
    """
    JSON serializer function for objects not serializable by default.

    Handles datetime, date, and time objects by converting them to ISO format strings.
    Used as the 'default' parameter in json.dumps() to handle these common types.

    Args:
        object: The object that couldn't be serialized by the default JSON encoder.

    Returns:
        str: ISO format string representation of datetime/date/time objects.

    Raises:
        TypeError: If the object type is not supported for serialization.
    """

    if isinstance(object, (datetime, date, time)):
        return object.isoformat()  # format datetimes as string for storage
    raise TypeError(f"Object of type {type(object)} is not JSON serializable")


async def set_redis_json(conn: CacheRedisConn, key: str, value, ttl: int):
    """
    Serialize a Python object to JSON and store it in Redis with TTL.

    Args:
        conn (CacheRedisConn): Redis connection for rebuildable cache data.
        key (str): Redis key to set.
        value: Python object to serialize and store.
        ttl (int): Time-to-live in seconds (key expires automatically).
    """

    jittered_ttl = jitter_ttl(ttl)
    payload = json.dumps(value, default=_json_default, separators=(",", ":"))
    await conn.client.setex(key, jittered_ttl, payload)


async def get_auth_state_json(conn: RedisConn, key: str):
    """Read short-lived auth state without involving cache invalidation.

    Args:
        conn (RedisConn): Versionless Redis connection for auth challenges.
        key (str): Stable auth-state key produced by ``build_auth_state_key``.

    Returns:
        The deserialized stored value, or ``None`` when the challenge expired.
    """

    return await get_redis_json(conn, key)


async def consume_auth_state_json(conn: RedisConn, key: str):
    """Atomically read and delete a one-use auth-state value."""

    raw_data = await conn.client.getdel(key)
    return json.loads(raw_data) if raw_data else None


async def set_auth_state_json(conn: RedisConn, key: str, value, ttl: int):
    """Store auth state with its exact security TTL, never cache TTL jitter.

    Args:
        conn (RedisConn): Versionless Redis connection for auth challenges.
        key (str): Stable auth-state key produced by ``build_auth_state_key``.
        value: JSON-serializable challenge data to store.
        ttl (int): Exact challenge lifetime in seconds.
    """

    payload = json.dumps(value, default=_json_default, separators=(",", ":"))
    await conn.client.setex(key, ttl, payload)


def jitter_ttl(ttl: int, pct: float = 0.10, min_ttl: int = 60) -> int:
    """
    Return a TTL jittered by pct% to avoid synchronized expirations

    Jittering spreads key expirations over a small random window, smoothing load and
    improving cache hit stability.

    Args:
        ttl (int): Base time-to-live in seconds.
        pct (float): Maximum proportional variation applied to ``ttl``.
        min_ttl (int): Lower bound for the returned TTL in seconds.

    Returns:
        int: Jittered TTL in seconds.
    """

    if ttl <= 0:
        raise ValueError(f"ttl must be > 0 (got {ttl})")

    random_factor = random.uniform(1 - pct, 1 + pct)  # e.g., 0.9 .. 1.1 for ±10%
    jittered = int(round(ttl * random_factor))  # round the product to an int
    return max(min_ttl, jittered)


def _to_param_str(val) -> str:
    """
    Convert a parameter value to its string representation for Redis key building.

    Handles different data types by converting them to consistent string formats:
    - datetime/date/time objects: ISO format strings
    - lists/tuples: comma-separated values
    - booleans: 'true' or 'false' strings
    - other types: string conversion

    Args:
        val: The parameter value to convert to string.

    Returns:
        str: String representation of the parameter value.
    """

    # Convert param data to a string
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()
    if isinstance(val, (list, tuple)):
        return ",".join(_to_param_str(x) for x in val)
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def build_redis_key(
    service: str,
    resource: str,
    params: dict | None = None,
    player_version: int | None = None,
) -> str:
    """
    Build a consistent Redis key string.

    Args:
        service (str): The service or namespace prefix, e.g. "cr_api" or "mongo"
        resource (str): The type of data or entity, e.g. "decks", "player", "leaked-elixir".
        params (dict): Additional key-value pairs describing this cache entry.
                These will be sorted and appended as 'key=value' segments.
                e.g. {"player_tag": "YYRJQY28", "start_date": "2025-08-01", "end_date": 2025-08-01})
        player_version (int | None): syncVersion of the player this entry belongs to.
                A new sync with new battles raises it, so new requests build new
                keys and only that player's old entries stop being used (they
                expire through their TTL). params has to contain the player tag.
                Data without a player version relies on its TTL alone.
    Returns:
        str: A Redis key in the format 'pv<version>:service:resource:param1=val1:...'
            for player data, otherwise 'service:resource:param1=val1:...'.
    """

    prefix = [f"pv{player_version}"] if player_version is not None else []

    # Sort params to keep key deterministic even if order changes
    parts = prefix + [service, resource]

    if params:  # Only append params to key if they exist
        for key, val in sorted(params.items()):
            # Use quote to get rid of and encode delimiters like '_', ":", ...
            val_stripped = str(val).lstrip(
                "#"
            )  # Remove leading '#', if player tag is in params
            key_str = quote(str(key), safe="")
            val_str = quote(_to_param_str(val_stripped), safe="")
            parts.append(f"{key_str}={val_str}")

    key = ":".join(parts)  # Build key string
    # Check if the key is not too long (bytes)
    if len(key.encode("utf-8")) < 512:
        return key

    # Uniquely hash key if it is too long. The version prefix stays readable,
    # so a player's entries still change keys with every new sync version.
    return ":".join(prefix + [service, hashlib.md5(key.encode("utf-8")).hexdigest()])


def build_auth_state_key(resource: str, challenge_id: str) -> str:
    """Build a stable, namespaced key for a short-lived auth challenge.

    Auth keys carry no version and live in the separate, non-evicting auth
    state Redis: cache invalidation must never invalidate a CAPTCHA or Word Guess
    challenge that is still within its promised lifetime.

    Args:
        resource (str): Fixed challenge category, such as ``captcha`` or ``word_guess``.
        challenge_id (str): Client-visible UUID that identifies one challenge.

    Returns:
        str: A delimiter-safe key in the ``auth:<resource>:<id>`` namespace.
    """

    return f"auth:{resource}:{quote(challenge_id, safe='')}"
