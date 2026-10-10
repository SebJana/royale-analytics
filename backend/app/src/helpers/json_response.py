"""JSON responses from encoded bytes, which skip FastAPI's encoder.

A route that returns a dict makes FastAPI walk every value with
jsonable_encoder and encode the copy with json.dumps, on the event loop.
Here a cache hit sends the stored body unchanged (one Redis GET, no decoding
or encoding), and a miss encodes once with orjson, then stores and sends the
same bytes, so a hit and a miss send identical bodies.

Payloads must stay within what orjson encodes like JSONResponse:
- Dicts with string keys, lists, strings, numbers, booleans, None and
  datetimes. orjson raises for sets, Decimals, pydantic models and non-string
  keys, which jsonable_encoder would convert.
- Datetimes naive or UTC, which orjson writes like isoformat().
- No NaN or infinity: JSONResponse refuses them, orjson writes null.

Cache keys of whole bodies use resource names of their own (a "Response"
suffix), since an entry stored in another format under the same key would be
sent as the body.

NOTE The shared lists (/cards, /game_modes) are sent as the data scraper
stores them. Its json.dumps escapes non-ASCII as \\uXXXX, which parses to
the same value as plain UTF-8. Keep its output valid JSON of the same shape.
"""

import orjson
from fastapi import Response

from core.route_timing import note_cache_lookup
from redis_service import CacheRedisConn, jitter_ttl

# Content type of every response built here.
MEDIA_TYPE = "application/json"


def json_response(payload) -> Response:
    """Encode payload with orjson into a response.

    Args:
        payload: Dicts, lists, strings, numbers, booleans, None and datetimes.
            Keys have to be strings.

    Returns:
        Response: The encoded body with the JSON media type.

    Raises:
        orjson.JSONEncodeError: For a type orjson cannot encode, such as a
            set, a Decimal or a non-string key.
    """

    return Response(content=orjson.dumps(payload), media_type=MEDIA_TYPE)


async def get_cached_response(conn: CacheRedisConn, key: str) -> Response | None:
    """Return the cached body under key as a response, None on a miss.

    Counts the lookup for the route's cache hit rate.

    Args:
        conn: The cache Redis.
        key: Key of a body stored by ``cache_json_response`` (or, for the
            shared lists, by the data scraper).

    Returns:
        Response | None: The stored body, sent as it is, or None.
    """

    # The client decodes to str; Response encodes it back to the same UTF-8
    # bytes in C, far below the cost of parsing the JSON.
    body = await conn.client.get(key)
    note_cache_lookup(body is not None)
    return None if body is None else Response(content=body, media_type=MEDIA_TYPE)


async def cache_json_response(
    conn: CacheRedisConn, key: str, payload, ttl: int
) -> Response:
    """Encode payload once, cache the bytes and return them as the response.

    Args:
        conn: The cache Redis.
        key: Key the body is stored under.
        payload: The complete response, as for ``json_response``.
        ttl: Seconds the entry lives, jittered like every cache entry.

    Returns:
        Response: The same bytes that were cached.

    Raises:
        orjson.JSONEncodeError: As in ``json_response``.
        RedisError: If the cache cannot be written.
    """

    body = orjson.dumps(payload)
    await conn.client.setex(key, jitter_ttl(ttl), body)
    return Response(content=body, media_type=MEDIA_TYPE)
