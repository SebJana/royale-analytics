"""Scraping capacity estimate shared by the data scraper and the API.

The data scraper knows its usable keys and the number of tracked players, so it
computes the capacity and publishes it to Redis. The API reads the published
values to decide whether one more player can be tracked; it never sees the
scraper's key pool itself.

The model: every battle sync and every profile refresh costs one Clash Royale
request. The scraper pool can start ``usable_keys * per_key_rps`` requests per
second; a utilization factor below 1 keeps headroom for retries, 429 cooldowns,
and card refreshes. Battle syncs and profile refreshes share that rate; both
get longer as the load rises, profiles much more so (see the data scraper's
intervals module), since a late profile is only stale while a late battle
sync can lose battles.

The two outputs use different loads on purpose:
- The base interval follows the actual mix of players. Idle players stretch
  their own intervals, and the requests they leave unused shorten the base for
  everyone else.
- The admission limit assumes every player is active, at full load. Idle players start
  playing again without notice (evenings, a new season), and players admitted
  on a quiet day must still fit within the longest interval then.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, asdict

from redis.asyncio import Redis

CAPACITY_KEY = "crsched:capacity"

# The base interval is searched to this precision. A second is far below the
# 5 minute reconciliation period that recomputes it anyway.
BASE_INTERVAL_PRECISION_S = 1.0


@dataclass(frozen=True)
class Capacity:
    """Capacity of the scraper pool for the current number of tracked players.

    base_interval_s is the battle sync interval the current mix of players can
    get right now. battle_demand is what that mix costs at this base; it exceeds
    battle_rate only when even the longest interval is not enough. max_players
    is the most players the pool can sync within the admission limit; None means
    unknown (no usable keys yet), in which case nothing is rejected.
    """

    active_players: int
    usable_keys: int
    request_rate: float
    battle_rate: float
    battle_demand: float
    base_interval_s: float
    max_players: int | None
    updated_at: float


def compute_capacity(
    active_players: int,
    usable_keys: int,
    per_key_rps: float,
    utilization: float,
    profile_max_age_s: float,
    min_interval_s: float,
    max_interval_s: float,
    battle_demand: Callable[[float], float],
    profile_demand: Callable[[float], float],
) -> Capacity:
    """Compute the base battle interval and the admission limit.

    Args:
        active_players (int): Currently tracked players.
        usable_keys (int): Usable keys in the scraper pool.
        per_key_rps (float): Requests per second each key may start.
        utilization (float): Share of the raw request rate that is planned for
            (0..1). The rest is headroom.
        profile_max_age_s (float): Shortest profile refresh period at full
            load. Admission assumes every player is refreshed this often.
        min_interval_s (float): Shortest battle interval; a small deployment
            never syncs more often than this.
        max_interval_s (float): Longest battle interval that still keeps every
            player within the battle log window.
        battle_demand (Callable[[float], float]): Battle requests per second
            the tracked players need at a given base interval. Must not
            increase with a longer base.
        profile_demand (Callable[[float], float]): Profile requests per second
            at a given base interval, with the same requirement.

    Returns:
        Capacity: The estimate, stamped with the current time.
    """

    request_rate = usable_keys * per_key_rps * utilization

    def fits(base_s: float) -> bool:
        return battle_demand(base_s) + profile_demand(base_s) <= request_rate

    # The shortest base whose battle and profile demand fit the request rate.
    # Both only fall as the base grows, so a bisection finds it.
    if fits(min_interval_s):
        base_interval_s = min_interval_s
    elif not fits(max_interval_s):
        # More demand than the keys can serve at all: everyone gets the
        # longest interval and is late anyway.
        base_interval_s = max_interval_s
    else:
        low, high = min_interval_s, max_interval_s
        while high - low > BASE_INTERVAL_PRECISION_S:
            middle = (low + high) / 2
            if fits(middle):
                high = middle
            else:
                low = middle
        base_interval_s = high
    battle_rate = max(request_rate - profile_demand(base_interval_s), 0.0)

    max_players = None
    if request_rate > 0:
        # Admission limit: the most players N that, all of them active, still
        # fit at the longest interval A. At A every player costs the same
        # one battle request, since the shortened interval of very active
        # players fades out towards A.
        #   R = request_rate, requests per second the keys may start
        #   P = profile_max_age_s, every player costs one profile request
        #       per P seconds (the stretched profile interval at full load)
        #   A = max_interval_s, seconds within which every player has to be
        #       synced once
        # N players need N / P requests per second for profiles, which leaves
        # R - N / P for battle syncs. Syncing each of the N players once then
        # takes N / (R - N / P) seconds, and that has to stay within A:
        #   N / (R - N / P) <= A
        #   N <= A * R - A * N / P        (multiply by R - N / P > 0)
        #   N * (1 + A / P) <= A * R
        #   N <= A * R / (1 + A / P)
        # Example with the default settings and 1 key at 1 request/s:
        # R = 0.8, A = 3600 s, P = 604800 s (7 days)
        # -> N <= 3600 * 0.8 / (1 + 3600 / 604800) = 2862 players
        max_players = math.floor(
            max_interval_s * request_rate / (1 + max_interval_s / profile_max_age_s)
        )

    return Capacity(
        active_players=active_players,
        usable_keys=usable_keys,
        request_rate=request_rate,
        battle_rate=battle_rate,
        battle_demand=battle_demand(base_interval_s),
        base_interval_s=base_interval_s,
        max_players=max_players,
        updated_at=time.time(),
    )


async def publish_capacity(redis: Redis, capacity: Capacity):
    """Store the capacity estimate for the API (and the status output)."""

    # Redis hashes hold strings; "" stands for None.
    fields = {
        key: "" if value is None else str(value)
        for key, value in asdict(capacity).items()
    }
    await redis.hset(CAPACITY_KEY, mapping=fields)


async def read_capacity(redis: Redis, max_age_s: float) -> Capacity | None:
    """Read the published estimate.

    Args:
        redis (Redis): Client for redis-key-store with ``decode_responses=True``.
        max_age_s (float): Older estimates are ignored, e.g. while the scraper
            is down and the number no longer reflects its keys.

    Returns:
        Capacity | None: The estimate, or None if missing or outdated.
    """

    fields = await redis.hgetall(CAPACITY_KEY)
    if not fields or "updated_at" not in fields:
        return None
    if time.time() - float(fields["updated_at"]) > max_age_s:
        return None
    return Capacity(
        active_players=int(fields["active_players"]),
        usable_keys=int(fields["usable_keys"]),
        request_rate=float(fields["request_rate"]),
        battle_rate=float(fields["battle_rate"]),
        battle_demand=float(fields["battle_demand"]),
        base_interval_s=float(fields["base_interval_s"]),
        max_players=int(fields["max_players"]) if fields["max_players"] else None,
        updated_at=float(fields["updated_at"]),
    )
