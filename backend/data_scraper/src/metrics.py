"""Scraper monitoring: counters, the Redis snapshot and history, and the dashboard.

Monitoring is for administrators with a shell on the deployed machine only.
The latest snapshot is stored in redis-key-store (read by status.py). A capped
list of per-minute samples next to it keeps the recent history across scraper
restarts. A small read-only HTTP server serves both, plus a dashboard page
that charts the history. Compose publishes it on the host's loopback
interface only; none of it is reachable through nginx or the website. The
API's route timings, which the API writes to the same Redis, are charted
there too (/api-history), as the API has no private port of its own.
"""

import asyncio
import bisect
import contextlib
import json
import logging
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from redis.asyncio import Redis
from redis.exceptions import RedisError

from api_key_store import KeyStore
from scrape_schedule import (
    API_DURATION_BOUNDS_S,
    API_METRICS_HISTORY_KEY,
    SCRAPER_METRICS_HISTORY_KEY,
    METRICS_KEY,
    Capacity,
    Schedule,
)
from settings import settings

logger = logging.getLogger(__name__)

# Counters are kept per time bucket, so the window slides in steps of this size
_BUCKET_S = 10

# Built by backend/data_scraper/dashboard (npm run build). The Dockerfile
# places it at /app/dashboard/dist, which resolves to the same relative path.
_DASHBOARD_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
}


def _load_dashboard(dist: Path) -> dict[str, tuple[str, bytes]]:
    """Read the built dashboard into memory, keyed by URL path.

    Only these paths are served, so a request can never reach another file.
    The page is static and fetches its data from the server.
    """

    if not (dist / "index.html").is_file():
        logger.warning(
            "Dashboard not built, run npm ci && npm run build in %s", dist.parent
        )
        return {}
    files = {}
    for file in dist.rglob("*"):
        content_type = _CONTENT_TYPES.get(file.suffix)
        if file.is_file() and content_type:
            files["/" + file.relative_to(dist).as_posix()] = (
                content_type,
                file.read_bytes(),
            )
    files["/"] = files["/dashboard"] = files["/index.html"]
    if "/api.html" in files:
        files["/api"] = files["/api.html"]
    return files


_DASHBOARD_FILES = _load_dashboard(_DASHBOARD_DIST)

# How history samples merge when a long range is reduced to fewer points.
# Counters add up, worst-case gauges keep their maximum, and every other
# field keeps the newest value of the merged samples.
_SUM_FIELDS = ("spanS", "battlesInserted", "possibleGaps")
_OUTCOME_FIELDS = ("battleOutcomes", "profileOutcomes")
_MAX_FIELDS = (
    "battleLatenessS",
    "battleDurationS",
    "profileLatenessS",
    "battlesDue",
    "profilesDue",
    "battlesOldestDueS",
    "profilesOldestDueS",
    "maintenance",
    "redisMemory",
    "mongoUnreachable",
    "mongoPingMs",
    "mongoConnections",
)
# Histograms add up bucket by bucket, so percentiles stay exact to the bucket
# when samples merge. Percentiles of percentiles would not.
_HIST_FIELDS = ("battleLatenessHist", "battleDurationHist")

# NOTE Upper bounds (seconds) of the histogram buckets, plus one overflow
# bucket. Stored samples refer to buckets by index, so changing a list
# misreads the existing history until it has rotated out.
LATENESS_BOUNDS_S = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600)
DURATION_BOUNDS_S = (0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 20, 30, 60, 90)


def _observe(hist: list[int], bounds: tuple, value: float):
    hist[bisect.bisect_left(bounds, value)] += 1


def _add_hist(total: list[int], hist: list[int] | None):
    for index, count in enumerate(hist or []):
        total[index] += count


def percentile(hist: list[int] | None, bounds: tuple, q: float) -> float | None:
    """Estimate a percentile from a bucket histogram.

    Interpolates linearly inside the bucket that holds the rank, which is how
    Prometheus' histogram_quantile estimates it.

    Args:
        hist: Counts per bucket; the last entry counts values above all bounds.
        bounds: Upper bucket bounds, ascending.
        q: Quantile between 0 and 1, e.g. 0.95.

    Returns:
        float | None: The estimate in seconds, the highest bound if the rank
        falls into the overflow bucket, or None without observations.
    """

    total = sum(hist or [])
    if not total:
        return None
    rank = q * total
    seen = 0
    for index, count in enumerate(hist):
        if count and seen + count >= rank:
            if index == len(bounds):
                return float(bounds[-1])
            lower = bounds[index - 1] if index else 0.0
            return lower + (bounds[index] - lower) * (rank - seen) / count
        seen += count
    return float(bounds[-1])


def _new_stats() -> dict:
    return {
        "outcomes": Counter(),
        "inserted": 0,
        "lateness": 0.0,
        "duration": 0.0,
        "latenessHist": [0] * (len(LATENESS_BOUNDS_S) + 1),
        "durationHist": [0] * (len(DURATION_BOUNDS_S) + 1),
        "gaps": 0,
    }


class Metrics:
    """Counts job outcomes over the last METRICS_WINDOW seconds.

    All workers run on one event loop and ``record`` contains no ``await``,
    so no lock is needed.
    """

    def __init__(self):
        self.started_at = time.time()
        # bucket index -> kind ("battles"/"profiles") -> counters
        self._buckets: dict[int, dict] = {}
        self.capacity: Capacity | None = None
        self.last_reconcile_at: float | None = None
        # Latest snapshot, served by the status endpoint without a Redis read
        self.latest: dict = {}
        # Since start instead of per window: a gap is rare, and one per hour
        # already matters.
        self.possible_gaps = 0
        # Counts since the last history sample. Separate from the buckets, so
        # consecutive samples never count the same job twice.
        self._sample = self._empty_sample()

    @staticmethod
    def _empty_sample() -> dict:
        return {kind: _new_stats() for kind in ("battles", "profiles")}

    def record(
        self,
        kind: str,
        outcome: str,
        inserted: int,
        lateness_s: float,
        duration_s: float,
        possible_gap: bool = False,
    ):
        """Count one finished job.

        Args:
            kind: Schedule name, "battles" or "profiles".
            outcome: The job's ``JobResult.outcome``.
            inserted: Battles the job inserted.
            lateness_s: Seconds between the player's due time and its claim.
            duration_s: Seconds the job ran, including key waits.
            possible_gap: Whether the battle log no longer reached back to
                the previous sync.
        """

        self.possible_gaps += possible_gap
        bucket = self._buckets.setdefault(
            int(time.time() // _BUCKET_S), defaultdict(_new_stats)
        )
        for stats in (bucket[kind], self._sample[kind]):
            stats["outcomes"][outcome] += 1
            stats["inserted"] += inserted
            stats["lateness"] = max(stats["lateness"], lateness_s)
            stats["duration"] = max(stats["duration"], duration_s)
            _observe(stats["latenessHist"], LATENESS_BOUNDS_S, lateness_s)
            _observe(stats["durationHist"], DURATION_BOUNDS_S, duration_s)
        self._sample[kind]["gaps"] += possible_gap

    def take_sample_counts(self) -> dict:
        """Return the job counts since the previous call and start new ones."""

        counts, self._sample = self._sample, self._empty_sample()
        return counts

    def window(self) -> dict:
        """Sum the buckets inside the window and drop older ones."""

        oldest = int((time.time() - settings.METRICS_WINDOW) // _BUCKET_S) + 1
        for index in [index for index in self._buckets if index < oldest]:
            del self._buckets[index]

        totals = {}
        for kind in ("battles", "profiles"):
            merged = _new_stats()
            for bucket in self._buckets.values():
                if kind in bucket:
                    stats = bucket[kind]
                    merged["outcomes"].update(stats["outcomes"])
                    merged["inserted"] += stats["inserted"]
                    merged["lateness"] = max(merged["lateness"], stats["lateness"])
                    merged["duration"] = max(merged["duration"], stats["duration"])
                    _add_hist(merged["latenessHist"], stats["latenessHist"])
                    _add_hist(merged["durationHist"], stats["durationHist"])
            totals[kind] = {
                "jobs": sum(merged["outcomes"].values()),
                "outcomes": dict(merged["outcomes"]),
                "inserted": merged["inserted"],
                "maxClaimLatenessS": round(merged["lateness"], 1),
                "maxDurationS": round(merged["duration"], 2),
                **_percentile_fields(
                    merged["latenessHist"],
                    merged["lateness"],
                    merged["durationHist"],
                    merged["duration"],
                ),
            }
        return totals


def _percentile_fields(
    lateness_hist: list[int] | None,
    max_lateness_s: float | None,
    duration_hist: list[int] | None,
    max_duration_s: float | None,
) -> dict:
    """p50/p95/p99 of claim lateness and job duration, rounded for JSON."""

    fields = {}
    for name, hist, bounds, maximum in (
        ("ClaimLatenessS", lateness_hist, LATENESS_BOUNDS_S, max_lateness_s),
        ("DurationS", duration_hist, DURATION_BOUNDS_S, max_duration_s),
    ):
        for q in (50, 95, 99):
            value = percentile(hist, bounds, q / 100)
            # Interpolation inside the top bucket can overshoot the largest
            # observation, most visibly with few jobs.
            if value is not None and maximum is not None:
                value = min(value, maximum)
            fields[f"p{q}{name}"] = None if value is None else round(value, 2)
    return fields


async def build_snapshot(
    metrics: Metrics,
    battles: Schedule,
    profiles: Schedule,
    key_store: KeyStore,
    workers: int,
    mongo: dict | None = None,
) -> dict:
    """Collect everything the status outputs show into one JSON-ready dict."""

    info = await key_store.redis.info("memory")
    capacity = metrics.capacity
    return {
        "updatedAt": time.time(),
        "startedAt": metrics.started_at,
        "windowS": settings.METRICS_WINDOW,
        "jobs": metrics.window(),
        "schedules": {
            "battles": await battles.stats(),
            "profiles": await profiles.stats(),
        },
        "capacity": (
            {
                "activePlayers": capacity.active_players,
                "maxPlayers": capacity.max_players,
                "baseIntervalS": round(capacity.base_interval_s),
                "requestRate": round(capacity.request_rate, 2),
                "battleRate": round(capacity.battle_rate, 2),
                "battleDemand": round(capacity.battle_demand, 2),
            }
            if capacity
            else None
        ),
        "keys": await key_store.inventory(),
        "maintenance": await key_store.in_maintenance(),
        "workers": workers,
        "lastReconcileAt": metrics.last_reconcile_at,
        "possibleGaps": metrics.possible_gaps,
        # ok is False when the health check failed or timed out
        "mongo": mongo,
        "redis": {
            "usedMemory": info.get("used_memory"),
            "maxMemory": info.get("maxmemory"),
        },
    }


async def publish_snapshot(metrics: Metrics, key_store: KeyStore, snapshot: dict):
    """Keep the snapshot for the endpoint and write it for the status CLI."""

    metrics.latest = snapshot
    # A plain string key: the CLI always reads the whole snapshot at once.
    await key_store.redis.set(METRICS_KEY, json.dumps(snapshot))


def build_history_sample(counts: dict, snapshot: dict, span_s: float) -> dict:
    """Combine the job counts of one sample period with the snapshot's gauges.

    Args:
        counts: Job counts since the previous sample, from
            ``Metrics.take_sample_counts``.
        snapshot: The snapshot built at the end of the period.
        span_s: Seconds the counts cover. The dashboard divides by it, so a
            period stretched by a failed snapshot still plots the right rate.

    Returns:
        dict: A flat, JSON-ready sample. Fields merge as listed in
        ``_SUM_FIELDS``, ``_OUTCOME_FIELDS`` and ``_MAX_FIELDS``.
    """

    schedules = snapshot["schedules"]
    capacity = snapshot["capacity"] or {}
    keys = snapshot["keys"]
    mongo = snapshot.get("mongo") or {}
    battles, profiles = counts["battles"], counts["profiles"]
    return {
        "t": round(snapshot["updatedAt"], 1),
        "spanS": round(span_s, 1),
        "battleOutcomes": dict(battles["outcomes"]),
        "profileOutcomes": dict(profiles["outcomes"]),
        "battlesInserted": battles["inserted"],
        "possibleGaps": battles["gaps"],
        "battleLatenessS": round(battles["lateness"], 1),
        "battleDurationS": round(battles["duration"], 2),
        "profileLatenessS": round(profiles["lateness"], 1),
        "battleLatenessHist": battles["latenessHist"],
        "battleDurationHist": battles["durationHist"],
        "battlesScheduled": schedules["battles"]["scheduled"],
        "profilesScheduled": schedules["profiles"]["scheduled"],
        "battlesDue": schedules["battles"]["due"],
        "profilesDue": schedules["profiles"]["due"],
        "battlesOldestDueS": round(schedules["battles"]["oldest_due_lateness_s"], 1),
        "profilesOldestDueS": round(schedules["profiles"]["oldest_due_lateness_s"], 1),
        "activePlayers": capacity.get("activePlayers"),
        "maxPlayers": capacity.get("maxPlayers"),
        "baseIntervalS": capacity.get("baseIntervalS"),
        "requestRate": capacity.get("requestRate"),
        "battleRate": capacity.get("battleRate"),
        "battleDemand": capacity.get("battleDemand"),
        "keysUsable": keys["usable"],
        "keysInvalid": keys["invalid"],
        "keysConfigured": keys["configured"],
        "workers": snapshot["workers"],
        "maintenance": int(bool(snapshot["maintenance"])),
        "redisMemory": snapshot["redis"]["usedMemory"],
        "battlesTotal": mongo.get("battlesTotal"),
        "mongoUnreachable": int(not mongo.get("ok", True)),
        "mongoPingMs": mongo.get("pingMs"),
        "mongoConnections": mongo.get("connections"),
        "mongoTotalSize": mongo.get("totalSize"),
        "mongoIndexSize": mongo.get("indexSize"),
        "mongoDataSize": mongo.get("dataSize"),
    }


async def append_history(redis: Redis, sample: dict):
    """Append a sample and drop the ones older than the retention."""

    max_samples = math.ceil(
        settings.METRICS_HISTORY_RETENTION / settings.METRICS_HISTORY_INTERVAL
    )
    # MULTI keeps the list capped even if the scraper stops between the two.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.rpush(
            SCRAPER_METRICS_HISTORY_KEY, json.dumps(sample, separators=(",", ":"))
        )
        pipe.ltrim(SCRAPER_METRICS_HISTORY_KEY, -max_samples, -1)
        await pipe.execute()


def _merge_samples(samples: list[dict]) -> dict:
    merged = dict(samples[-1])
    for field in _SUM_FIELDS:
        merged[field] = sum(sample.get(field) or 0 for sample in samples)
    for field in _OUTCOME_FIELDS:
        outcomes = Counter()
        for sample in samples:
            outcomes.update(sample.get(field) or {})
        merged[field] = dict(outcomes)
    for field in _MAX_FIELDS:
        values = [sample[field] for sample in samples if sample.get(field) is not None]
        merged[field] = max(values) if values else None
    for field in _HIST_FIELDS:
        hists = [sample[field] for sample in samples if sample.get(field)]
        total = [0] * max((len(hist) for hist in hists), default=0)
        for hist in hists:
            _add_hist(total, hist)
        merged[field] = total
    return merged


def _with_percentiles(sample: dict) -> dict:
    """Replace a sample's histograms by the percentiles the dashboard plots."""

    sample = dict(sample)
    lateness = sample.pop("battleLatenessHist", None)
    duration = sample.pop("battleDurationHist", None)
    fields = _percentile_fields(
        lateness, sample.get("battleLatenessS"), duration, sample.get("battleDurationS")
    )
    for key, value in fields.items():
        sample["battle" + key[0].upper() + key[1:]] = value
    return sample


def downsample(
    samples: list[dict],
    range_s: float,
    max_points: int,
    merge: Callable[[list[dict]], dict] = _merge_samples,
) -> tuple[list[dict], float]:
    """Merge samples into time aligned buckets, at most ``max_points`` of them.

    Buckets follow the clock instead of the sample count, so a period without
    samples (scraper stopped) stays an empty stretch in the charts.

    Args:
        samples: Samples oldest first, as stored by ``append_history``.
        range_s: Length of the range the samples cover.
        max_points: Upper bound for the number of buckets in the range.
        merge: Combines the samples of one bucket into one.

    Returns:
        tuple[list[dict], float]: The merged samples, oldest first, and the
        bucket length in seconds.
    """

    interval = settings.METRICS_HISTORY_INTERVAL
    bucket_s = max(1, math.ceil(range_s / max_points / interval)) * interval
    if bucket_s <= interval:
        return samples, interval
    groups: dict[int, list[dict]] = {}
    for sample in samples:
        groups.setdefault(int(sample["t"] // bucket_s), []).append(sample)
    return [merge(group) for group in groups.values()], bucket_s


async def read_history(redis: Redis, range_s: float) -> dict:
    """Return the samples of the last ``range_s`` seconds, downsampled.

    Histograms are replaced by their percentiles, e.g. ``battleP95DurationS``.

    Args:
        redis: The key store Redis that holds the history.
        range_s: Length of the range. Clamped to 1 minute up to the retention.

    Returns:
        dict: ``rangeS``, ``intervalS`` (raw sample length), ``bucketS``
        (length after downsampling), ``now`` and ``samples`` (oldest first).

    Raises:
        RedisError: If the key store Redis is unavailable.
    """

    range_s = min(max(range_s, 60), settings.METRICS_HISTORY_RETENTION)
    # Twice the expected count covers samples that ran short; the timestamp
    # filter below drops the surplus.
    count = 2 * math.ceil(range_s / settings.METRICS_HISTORY_INTERVAL)
    raw = await redis.lrange(SCRAPER_METRICS_HISTORY_KEY, -count, -1)
    now = time.time()
    samples = [
        sample for sample in map(json.loads, raw) if sample["t"] >= now - range_s
    ]
    samples, bucket_s = downsample(samples, range_s, settings.HISTORY_MAX_POINTS)
    return {
        "rangeS": range_s,
        "intervalS": settings.METRICS_HISTORY_INTERVAL,
        "bucketS": bucket_s,
        "now": now,
        # Percentiles are computed after merging, from the summed histograms.
        "samples": [_with_percentiles(sample) for sample in samples],
    }


# The API's busiest routes get their own p95 line. Four, like the colors the
# dashboard has; more lines become unreadable anyway.
API_CHARTED_ROUTES = 4
# Processed /api-history answers by (range, route, group) with the monotonic
# time they were built, reused for API_HISTORY_CACHE_S. Dashboards ask for a
# few fixed ranges, and expired entries are dropped on every build.
_api_history_cache: dict[tuple, tuple[float, dict]] = {}

# Routes of the auth flow (CAPTCHA, Word Guess, Fruit Buzz, security questions,
# removal token); every other route is regular traffic. The dashboard can
# chart either group on its own, so an attack on one shows apart from the other.
# NOTE Matches the /api prefix and the "/auth" router prefix in the API's
# main.py and routers/auth.py.
API_AUTH_ROUTE_PREFIX = "/api/auth/"

API_ROUTE_GROUPS = ("regular", "auth")


def route_group(route: str) -> str:
    """Return "auth" for a route of the auth flow, else "regular".

    Args:
        route: "METHOD /path/template", as the API records it.
    """

    path = route.split(" ", 1)[-1]
    return "auth" if path.startswith(API_AUTH_ROUTE_PREFIX) else "regular"


def _new_route() -> dict:
    return {"n": 0, "e4": 0, "e5": 0, "max": 0.0, "h": [], "ch": 0, "cm": 0}


def _add_route(total: dict, stats: dict):
    total["n"] += stats.get("n", 0)
    total["e4"] += stats.get("e4", 0)
    total["e5"] += stats.get("e5", 0)
    # Absent from samples written before the API counted cache lookups.
    total["ch"] += stats.get("ch", 0)
    total["cm"] += stats.get("cm", 0)
    total["max"] = max(total["max"], stats.get("max", 0.0))
    hist = stats.get("h") or []
    if len(total["h"]) < len(hist):
        total["h"] += [0] * (len(hist) - len(total["h"]))
    _add_hist(total["h"], hist)


def _merge_api_samples(samples: list[dict]) -> dict:
    """Add up the per-route counts and histograms of consecutive API samples."""

    routes: dict[str, dict] = {}
    for sample in samples:
        for route, stats in sample["routes"].items():
            _add_route(routes.setdefault(route, _new_route()), stats)
    return {
        "t": samples[-1]["t"],
        "spanS": round(sum(sample["spanS"] for sample in samples), 1),
        "routes": routes,
    }


def _api_summary(stats: dict) -> dict:
    """Request, error and cache lookup counts and p50/p95/p99/max seconds.

    ``cacheHits`` and ``cacheMisses`` count response cache lookups, so both
    stay 0 for a route without a response cache. A percentile past the top
    bucket bound only reads that bound, so ``p<q>Over`` marks it as a lower
    limit; it is left out otherwise to keep the samples small.
    """

    fields = {
        "requests": stats["n"],
        "e4": stats["e4"],
        "e5": stats["e5"],
        # Raw samples from before the API counted cache lookups lack both.
        "cacheHits": stats.get("ch", 0),
        "cacheMisses": stats.get("cm", 0),
        "maxS": round(stats["max"], 4) if stats["n"] else None,
    }
    hist = stats["h"]
    total = sum(hist)
    # Requests up to the top bound; the last histogram entry is the overflow.
    bounded = total - hist[-1] if len(hist) > len(API_DURATION_BOUNDS_S) else total
    for q in (50, 95, 99):
        value = percentile(hist, API_DURATION_BOUNDS_S, q / 100)
        # Interpolation inside the top bucket can overshoot the slowest request.
        if value is not None:
            value = round(min(value, stats["max"]), 4)
        fields[f"p{q}S"] = value
        if total and bounded < q / 100 * total:
            fields[f"p{q}Over"] = True
    return fields


async def read_api_history(
    redis: Redis, range_s: float, route: str | None = None, group: str | None = None
) -> dict:
    """Return the API's route timings of the last ``range_s`` seconds.

    Args:
        redis: The key store Redis the API writes its samples to.
        range_s: Length of the range. Clamped like ``read_history``.
        route: Chart and total only this route ("GET /api/cards"), None
            for all routes together.
        group: Without a route, chart and total only "regular" or "auth"
            routes; None for both.

    Returns:
        dict: ``rangeS``, ``intervalS``, ``bucketS`` and ``now`` as in
        ``read_history``; ``route`` and ``group``, what is charted, or None;
        ``samples`` (oldest first) with the counts and percentiles of that
        route or group per bucket, and the p95 of its busiest routes
        (``routeP95S``), for the charts; ``routes``, every route's totals and
        ``group`` over the whole range, most requested first, for the table,
        whatever is charted; and ``total``, the charted route(s) over the
        whole range.

    Raises:
        RedisError: If the key store Redis is unavailable.
    """

    # NOTE The API samples every ROUTE_METRICS_INTERVAL_S and keeps
    # ROUTE_METRICS_RETENTION_S, which match this history's settings.
    range_s = min(max(range_s, 60), settings.METRICS_HISTORY_RETENTION)
    key = (range_s, route, group)
    cached = _api_history_cache.get(key)
    if cached and time.monotonic() - cached[0] < settings.API_HISTORY_CACHE_S:
        return cached[1]
    count = 2 * math.ceil(range_s / settings.METRICS_HISTORY_INTERVAL)
    raw = await redis.lrange(API_METRICS_HISTORY_KEY, -count, -1)
    # Decoding and merging a long range is pure CPU; on this loop it would
    # hold up the scheduling and scraping that share it.
    history = await asyncio.to_thread(_build_api_history, raw, range_s, route, group)
    now = time.monotonic()
    for stale in [
        k
        for k, (at, _) in _api_history_cache.items()
        if now - at >= settings.API_HISTORY_CACHE_S
    ]:
        del _api_history_cache[stale]
    _api_history_cache[key] = (now, history)
    return history


def _build_api_history(
    raw: list[bytes], range_s: float, route: str | None, group: str | None
) -> dict:
    """Decode and summarize raw API samples, see ``read_api_history``."""

    now = time.time()
    samples = [
        sample for sample in map(json.loads, raw) if sample["t"] >= now - range_s
    ]
    whole = _merge_api_samples(samples) if samples else {"routes": {}}
    routes = [
        {"route": name, "group": route_group(name), **_api_summary(stats)}
        for name, stats in whole["routes"].items()
    ]
    routes.sort(key=lambda row: (-row["requests"], row["route"]))
    busiest = [
        row["route"] for row in routes if group is None or row["group"] == group
    ][:API_CHARTED_ROUTES]
    samples, bucket_s = downsample(
        samples, range_s, settings.HISTORY_MAX_POINTS, _merge_api_samples
    )
    charted = []
    for sample in samples:
        total = _charted_stats(sample["routes"], route, group)
        charted.append(
            {
                "t": sample["t"],
                "spanS": sample["spanS"],
                **_api_summary(total),
                # Absent from a bucket without requests, which breaks the line.
                "routeP95S": {
                    route: _api_summary(sample["routes"][route])["p95S"]
                    for route in busiest
                    if route in sample["routes"]
                },
            }
        )
    return {
        "rangeS": range_s,
        "intervalS": settings.METRICS_HISTORY_INTERVAL,
        "bucketS": bucket_s,
        "now": now,
        "route": route,
        "group": group,
        "samples": charted,
        "routes": routes,
        "total": _api_summary(_charted_stats(whole["routes"], route, group)),
    }


def _charted_stats(
    routes: dict[str, dict], route: str | None, group: str | None = None
) -> dict:
    """One route's counts, else one group's or all routes' added up.

    A route without requests yields zero counts, so its charts show the
    quiet stretch instead of a gap that would read as the API being down.
    """

    total = _new_route()
    for name, stats in routes.items():
        if route is not None:
            charted = name == route
        else:
            charted = group is None or route_group(name) == group
        if charted:
            _add_route(total, stats)
    return total


def _json_error(status: str, detail: str) -> tuple[str, str, bytes]:
    return status, "application/json", json.dumps({"detail": detail}).encode()


async def _route(
    metrics: Metrics, redis: Redis, method: str, target: str
) -> tuple[str, str, bytes]:
    """Return the status line, content type, and body for one request."""

    if method != "GET":
        return _json_error("405 Method Not Allowed", "GET only")
    url = urlsplit(target)
    if url.path in _DASHBOARD_FILES:
        content_type, body = _DASHBOARD_FILES[url.path]
        return "200 OK", content_type, body
    if url.path in ("/", "/dashboard"):
        return _json_error("503 Service Unavailable", "Dashboard not built")
    if url.path == "/status":
        body = json.dumps(metrics.latest, indent=2).encode()
        return "200 OK", "application/json", body
    if url.path in ("/history", "/api-history"):
        query = parse_qs(url.query)
        try:
            range_s = float(query.get("range", ["3600"])[0])
        except ValueError:
            range_s = math.nan
        if not math.isfinite(range_s):
            return _json_error("400 Bad Request", "range must be seconds")
        try:
            if url.path == "/history":
                history = await read_history(redis, range_s)
            else:
                route = query.get("route", [None])[0]
                group = query.get("group", [None])[0]
                if group is not None and group not in API_ROUTE_GROUPS:
                    return _json_error(
                        "400 Bad Request", "group must be regular or auth"
                    )
                history = await read_api_history(redis, range_s, route, group)
        except RedisError:
            logger.exception("History read failed")
            return _json_error("503 Service Unavailable", "Key store unavailable")
        return "200 OK", "application/json", json.dumps(history).encode()
    return _json_error("404 Not Found", "Not found")


async def _handle_status_request(
    metrics: Metrics,
    redis: Redis,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
):
    """Answer one request with the dashboard, the snapshot, or the history."""

    try:
        # Reads the headers too: closing a socket with unread input sends a
        # TCP reset, which can drop the end of a large response in transit.
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        request_line = head.split(b"\r\n", 1)[0]
        method, target, *_ = request_line.decode("latin-1").split(" ") + ["", ""]
        status, content_type, payload = await _route(metrics, redis, method, target)
        writer.write(
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            # The data changes every few seconds, so a cached copy is stale.
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n".encode() + payload
        )
        await writer.drain()
    except (
        asyncio.TimeoutError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        ConnectionError,
    ):
        pass
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), timeout=5)


async def start_status_server(
    metrics: Metrics, redis: Redis
) -> asyncio.base_events.Server:
    """Start the read-only status endpoint and dashboard on STATUS_PORT.

    Routes: ``/`` (scraper page) and ``/api`` (API page) with their
    ``/assets/``, ``/status`` (latest snapshot as JSON),
    ``/history?range=<seconds>`` (samples for the dashboard charts) and
    ``/api-history?range=<seconds>[&route=<route>][&group=regular|auth]``
    (the API's route timings, of all routes, one group, or one route).

    It listens on all interfaces inside the container, which Docker needs to
    forward the port. docker-compose.yml publishes it as 127.0.0.1:9100 only,
    so on the host it is reachable from the machine itself (or an SSH tunnel).

    Args:
        metrics: Holds the latest snapshot.
        redis: The key store Redis that holds the history.

    Returns:
        asyncio.base_events.Server: The listening server, closed on shutdown.
    """

    return await asyncio.start_server(
        lambda reader, writer: _handle_status_request(metrics, redis, reader, writer),
        host="0.0.0.0",
        port=settings.STATUS_PORT,
    )
