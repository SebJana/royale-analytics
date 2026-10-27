"""Times every API request per route, for the scraper's monitoring dashboard.

Guarantees:
- Each request counts once, under its method and route template ("GET
  /api/players/{player_tag}"), so tags and queries never become rows of their
  own. Requests no route matched share one row per method ("GET (no route)"),
  which keeps 404 probes apart from CORS preflights, answered before routing.
- Timing never fails a request: recording is a few dict and list updates
  per request, and publishing runs in its own task, where a Redis error only
  loses that minute's sample.
- The history stays bounded: one sample a minute, capped to the retention.
- Routes that answer from the response cache report each lookup with
  ``note_cache_lookup``, so every sample carries the route's cache hits and
  misses, and each minute's hit rates are also logged.

Samples go to the key store Redis, where the data scraper's status server
reads them (/api-history). Nothing here is reachable from outside: the API
serves no endpoint for it, and docker-compose.yml publishes the dashboard's
port on the host's loopback interface only.
"""

import asyncio
import bisect
import json
import math
import time
from contextvars import ContextVar

from redis.asyncio import Redis
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scrape_schedule import API_DURATION_BOUNDS_S, API_METRICS_HISTORY_KEY

# Stands in for the path of requests no route matched.
UNMATCHED = "(no route)"

# Status recorded for a request whose client went away before the response;
# nginx logs the same case as 499.
_CLIENT_CLOSED = 499

# [hits, misses] of the request being handled, None outside a request. The
# middleware owns the list; routes only increment it, which also reaches the
# middleware from a dependency run in a worker thread, as that thread's copied
# context still points to the same list.
_cache_lookups: ContextVar[list[int] | None] = ContextVar("cache_lookups", default=None)


def note_cache_lookup(hit: bool) -> None:
    """Count one response cache lookup of the current request.

    Only lookups that decide whether the response comes from the cache count;
    reads of shared lists for validation would inflate the hit rate.

    Args:
        hit: Whether the cache held the response.
    """

    lookups = _cache_lookups.get()
    if lookups is not None:
        lookups[0 if hit else 1] += 1


def _new_route() -> dict:
    return {
        "n": 0,
        "e4": 0,
        "e5": 0,
        "max": 0.0,
        "h": [0] * (len(API_DURATION_BOUNDS_S) + 1),
        "ch": 0,
        "cm": 0,
    }


class RouteTimings:
    """Request counts and response time histograms per route since the last sample.

    Requests and the publisher run on one event loop and ``record`` contains
    no ``await``, so no lock is needed.
    """

    def __init__(self):
        self._routes: dict[str, dict] = {}
        self._since = time.time()

    def record(
        self,
        route: str,
        status: int,
        duration_s: float,
        cache_hits: int = 0,
        cache_misses: int = 0,
    ) -> None:
        """Count one finished request.

        Args:
            route: "METHOD /path/template", or "METHOD (no route)".
            status: The response status; 499 when the client went away.
            duration_s: Seconds from receiving the request to the last byte
                of the response.
            cache_hits: Response cache lookups that found the response.
            cache_misses: Response cache lookups that did not.
        """

        stats = self._routes.get(route)
        if stats is None:
            stats = self._routes[route] = _new_route()
        stats["n"] += 1
        stats["e4"] += 400 <= status < 500
        stats["e5"] += status >= 500
        stats["max"] = max(stats["max"], duration_s)
        stats["h"][bisect.bisect_left(API_DURATION_BOUNDS_S, duration_s)] += 1
        stats["ch"] += cache_hits
        stats["cm"] += cache_misses

    def take_sample(self) -> dict:
        """Return the counts since the previous call as a sample, and start anew."""

        now = time.time()
        routes, self._routes = self._routes, {}
        span_s, self._since = now - self._since, now
        for stats in routes.values():
            stats["max"] = round(stats["max"], 4)
        return {"t": round(now, 1), "spanS": round(span_s, 1), "routes": routes}


class RouteTimingMiddleware:
    """Pure ASGI middleware that feeds every HTTP request into RouteTimings.

    Pure ASGI instead of BaseHTTPMiddleware: that one wraps the response in
    an extra task and stream on every request, for a timer that only needs
    the start and end.
    """

    def __init__(self, app: ASGIApp, timings: RouteTimings):
        self.app = app
        self.timings = timings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        # An exception before the response starts becomes a 500 further out.
        status = 500
        lookups = [0, 0]
        lookups_token = _cache_lookups.set(lookups)

        async def send_and_note_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_note_status)
        except asyncio.CancelledError:
            status = _CLIENT_CLOSED
            raise
        finally:
            _cache_lookups.reset(lookups_token)
            # The router writes the matched route into this scope, so after
            # the call it names the template rather than the concrete path.
            route = scope.get("route")
            path = getattr(route, "path", None)
            key = f"{scope['method']} {path or UNMATCHED}"
            self.timings.record(key, status, time.perf_counter() - started, *lookups)


async def publish_route_timings(
    timings: RouteTimings, redis: Redis, interval_s: float, retention_s: float
) -> None:
    """Append a sample every interval until cancelled, then one last time.

    Args:
        timings: The counts to sample.
        redis: The key store Redis the scraper reads the history from.
        interval_s: Seconds between samples.
        retention_s: Samples older than this are dropped.
    """

    max_samples = math.ceil(retention_s / interval_s)
    try:
        while True:
            await asyncio.sleep(interval_s)
            await _append(timings, redis, max_samples)
    finally:
        # Shielding lets the last partial minute reach Redis while shutdown
        # cancels this task.
        await asyncio.shield(_append(timings, redis, max_samples))


async def _append(timings: RouteTimings, redis: Redis, max_samples: int) -> None:
    # Written even without requests: the dashboard reads a missing sample as
    # the API being down, and an idle minute as zero requests.
    sample = timings.take_sample()
    _log_cache_hits(sample)
    try:
        # MULTI keeps the list capped even if the API stops between the two.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.rpush(
                API_METRICS_HISTORY_KEY, json.dumps(sample, separators=(",", ":"))
            )
            pipe.ltrim(API_METRICS_HISTORY_KEY, -max_samples, -1)
            await pipe.execute()
    except Exception as e:
        print(f"[WARNING] [METRICS] Could not store the route timings: {e}")


def _log_cache_hits(sample: dict) -> None:
    """Log the hit rate of every route that looked up its response cache."""

    rates = [
        f"{route} {stats['ch'] * 100 // (stats['ch'] + stats['cm'])}% "
        f"({stats['ch']}/{stats['ch'] + stats['cm']})"
        for route, stats in sorted(sample["routes"].items())
        if stats["ch"] + stats["cm"]
    ]
    if rates:
        print(
            f"[INFO] [CACHE] Hit rate over {sample['spanS']:.0f} s: " + ", ".join(rates)
        )
