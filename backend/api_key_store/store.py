"""Share Clash Royale API keys between requests, workers, and backend services.

Each named pool has its own keys. Redis is the common authority for
which key is ready, when it may be used again, and who currently holds it.
Only fingerprints and scheduling data go to Redis; raw keys stay in the local
environment and are added to the Authorization header by the ClashRoyaleAPI module.
"""

import asyncio
import hashlib
import logging
import math
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .names import env_key_prefix

logger = logging.getLogger(__name__)

# Leave time to process the response and release the Redis lease after HTTP ends.
LEASE_CLEANUP_MARGIN_S = 5.0
VALIDATION_LOCK_MARGIN_S = 60.0

# KEYS are the pool configuration, global key owner map, and the pool's
# inventory/scheduling records. ARGV contains the configuration ID, pool name,
# then every key fingerprint. The configuration ID includes the container boot
# ID, so a new launch validates its keys again. If several workers start at
# once, they register the same ID and share the resulting validation pass.
# Check ownership BEFORE removing old records: a key in both pools would
# otherwise have two independent limits and could be assigned simultaneously.
REGISTER = """
local old = redis.call('GET', KEYS[1])
-- Refuse a key already registered to the other pool.
-- Lua indexes ARGV from 1: configuration ID and pool name occupy 1 and 2,
-- so key fingerprints start at 3 in both loops below.
for i = 3, #ARGV do
    local owner = redis.call('HGET', KEYS[2], ARGV[i])
    if owner and owner ~= ARGV[2] then return 'duplicate_across_pools' end
end
if old ~= ARGV[1] then
    -- A new boot/configuration cannot keep the previous validation results.
    local previous = redis.call('SMEMBERS', KEYS[3])
    for _, id in ipairs(previous) do redis.call('HDEL', KEYS[2], id) end
    redis.call('DEL', KEYS[3], KEYS[4], KEYS[5], KEYS[6], KEYS[7], KEYS[8], KEYS[9], KEYS[10])
end
for i = 3, #ARGV do redis.call('HSET', KEYS[2], ARGV[i], ARGV[2]) end
if #ARGV > 2 then redis.call('SADD', KEYS[3], unpack(ARGV, 3)) end
redis.call('SET', KEYS[1], ARGV[1])
return 'ok'
"""

# KEYS hold status, next-use times, cooldowns, last-use order, the optional
# pool-wide limit, global maintenance state, lease prefix, sequence, and the
# configuration ID. ARGV contains owner, lease TTL, IDs, intervals, config ID.
# Redis runs this whole check/selection/write without another worker entering
# in between. Local Python locks would protect only one process.
ACQUIRE = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
-- A stale worker must stop using keys after another boot changed the pool.
if redis.call('GET', KEYS[9]) ~= ARGV[#ARGV] then return {-1, 0} end
local maintenance = redis.call('GET', KEYS[6])
-- Even when the next probe time passed, normal jobs wait for a successful probe.
if maintenance then return {0, math.max(0, tonumber(maintenance) - now)} end
local pool_next = tonumber(redis.call('GET', KEYS[5]) or '0')
local best, best_used, best_wait = 0, nil, 0
-- Owner and lease TTL occupy ARGV[1] and ARGV[2]; IDs start at 3 and precede three settings.
for i = 3, #ARGV - 3 do
    local id = ARGV[i]
    if redis.call('HGET', KEYS[1], id) == 'usable' then
        local lock = KEYS[7] .. id
        local next_at = tonumber(redis.call('HGET', KEYS[2], id) or '0')
        local cooldown = tonumber(redis.call('HGET', KEYS[3], id) or '0')
        local eligible = math.max(next_at, cooldown, pool_next)
        -- LRU is applied only among keys that are valid, free, and rate-ready.
        if redis.call('EXISTS', lock) == 0 and eligible <= now then
            local used = tonumber(redis.call('HGET', KEYS[4], id) or '0')
            if not best_used or used < best_used then best, best_used = i - 2, used end
        elseif eligible > now and (best_wait == 0 or eligible - now < best_wait) then
            best_wait = eligible - now
        end
    end
end
if best == 0 then return {0, best_wait} end
local id = ARGV[best + 2]
local lock = KEYS[7] .. id
-- The lease key expires if its worker dies; NX prevents an existing lease
-- from being replaced. The earlier availability check is safe within this script.
redis.call('SET', lock, ARGV[1], 'PX', ARGV[2], 'NX')
-- This shared sequence gives all workers the same ordering; wall-clock ties
-- and process-local counters cannot provide a reliable LRU order.
local seq = redis.call('INCR', KEYS[8])
redis.call('HSET', KEYS[4], id, seq)
-- Reserve the per-key and optional pool-wide next start times at acquisition.
redis.call('HSET', KEYS[2], id, now + tonumber(ARGV[#ARGV - 2]))
if tonumber(ARGV[#ARGV - 1]) > 0 then redis.call('SET', KEYS[5], now + tonumber(ARGV[#ARGV - 1])) end
return {best, 0}
"""

# The outcome and lease release are one Redis operation. A 429 or maintenance
# response must update the shared cooldown/circuit BEFORE the key becomes free.
# The owner token also stops a late response from releasing another job's lease
# after the original lease expired and was acquired again.
RELEASE = """
-- An expired lease may already belong to a different request. Do not update
-- its cooldown or status, and do not delete its replacement lease.
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
-- An old worker can release its own lease, but cannot change new inventory.
if redis.call('GET', KEYS[7]) ~= ARGV[9] then return redis.call('DEL', KEYS[1]) end
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local outcome = ARGV[3]
if outcome == 'rate_limited' then
    -- Retry-After is a lower bound; otherwise consecutive 429s grow the delay.
    local count = redis.call('HINCRBY', KEYS[4], ARGV[2], 1)
    local delay = math.min(tonumber(ARGV[5]), tonumber(ARGV[4]) * (2 ^ math.min(count - 1, 12)))
    delay = math.max(delay, tonumber(ARGV[6]))
    redis.call('HSET', KEYS[3], ARGV[2], now + delay)
elseif outcome == 'success' then
    -- One successful response resets this key's consecutive-429 counter.
    redis.call('HDEL', KEYS[4], ARGV[2])
elseif outcome == 'auth_failed' then
    -- Do not rotate a rejected token back into ordinary requests.
    redis.call('HSET', KEYS[2], ARGV[2], 'invalid')
elseif outcome == 'maintenance' then
    -- Maintenance applies to both pools, not only the key that saw it.
    local count = redis.call('INCR', KEYS[6])
    local delay = math.min(tonumber(ARGV[8]), tonumber(ARGV[7]) * (2 ^ math.min(count - 1, 12)))
    redis.call('SET', KEYS[5], now + delay)
end
return redis.call('DEL', KEYS[1])
"""

# Startup validation/probe locks also use owner tokens. If a slow validator's
# lock expired, it must not remove the lock acquired by the next validator.
UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class NoKeyAvailable(Exception):
    """No eligible key became available within the acquisition budget."""

    def __init__(self, retry_after: float = 1.0, reason: str = "busy"):
        self.reason = reason
        detail = {
            "busy": "No Clash Royale API key is currently available",
            "no_valid_keys": "No valid Clash Royale API keys are configured",
            "validation_pending": "Clash Royale API key validation is pending",
            "maintenance": "Clash Royale API is in maintenance",
        }.get(reason, "No Clash Royale API key is currently available")
        super().__init__(detail)
        self.retry_after = max(1.0, retry_after)


class KeyStoreUnavailable(Exception):
    """Redis coordination is unavailable or its inventory is not initialized."""


def keys_from_env(pool: str, environ=None) -> list[str]:
    """Load numbered entries for one pool, independent of the other pool.

    Numbering may have gaps after a key is removed. The helper checks both
    pools, while Redis also rejects cross-pool duplicates at startup.
    """
    environ = os.environ if environ is None else environ
    prefix = env_key_prefix(pool)
    numbered = sorted(
        (int(name[len(prefix) :]), value.strip())
        for name, value in environ.items()
        if name.startswith(prefix) and name[len(prefix) :].isdigit() and value.strip()
    )
    keys = [value for _, value in numbered]
    if not keys:
        raise ValueError(f"Configure at least one {prefix}<number> in .env")
    if len(set(keys)) != len(keys):
        raise ValueError(f"Duplicate API key in {pool} pool")
    return keys


@dataclass(frozen=True)
class KeyStoreConfig:
    """Intervals and retry limits used by every worker in one pool.

    requests_per_second is a per-key start interval. A zero pool rate disables
    the optional pool-wide cap. The overall request budget prevents a 429
    retry from starting a new full acquisition wait each time. Defaults for
    coordination behavior live here; callers pass their pool's rates.
    """

    requests_per_second: float
    pool_requests_per_second: float = 0.0
    acquisition_attempts: int = 20
    acquisition_backoff_s: float = 0.2
    request_budget_s: float = 30.0
    probe_timeout_s: float = 30.0
    lease_s: float = 60.0
    cooldown_initial_s: float = 2.0
    cooldown_max_s: float = 120.0
    retry_after_max_s: float = 300.0
    maintenance_probe_initial_s: float = 30.0
    maintenance_probe_max_s: float = 300.0

    def __post_init__(self):
        positive = (
            self.requests_per_second,
            self.acquisition_attempts,
            self.acquisition_backoff_s,
            self.request_budget_s,
            self.probe_timeout_s,
            self.lease_s,
            self.cooldown_initial_s,
            self.cooldown_max_s,
            self.retry_after_max_s,
            self.maintenance_probe_initial_s,
            self.maintenance_probe_max_s,
        )
        if any(value <= 0 for value in positive) or self.pool_requests_per_second < 0:
            raise ValueError("Invalid key store settings")
        if self.lease_s <= LEASE_CLEANUP_MARGIN_S:
            raise ValueError("Key lease must exceed its cleanup margin")
        if (
            self.cooldown_max_s < self.cooldown_initial_s
            or self.maintenance_probe_max_s < self.maintenance_probe_initial_s
        ):
            raise ValueError("Maximum delay must be at least the initial delay")


@dataclass(frozen=True)
class KeyLease:
    """The assigned token and the unique owner required to release its lock."""

    key: str
    key_id: str
    owner: str


class KeyStore:
    """Coordinate one named key pool through dedicated Redis.

    All instances for a pool must use the same keys and settings. Their raw
    key values remain local, while Redis holds only the SHA-256 IDs. The
    configuration ID includes keys, settings, and boot generation so a stale
    worker fails closed after a deployment changes the inventory. Different
    names create different pools; repeated use of a name intentionally joins
    the same pool across workers.
    """

    def __init__(
        self, pool: str, keys: list[str], redis: Redis, config: KeyStoreConfig
    ):
        if not isinstance(pool, str) or not pool.strip():
            raise ValueError("Pool name must be a non-empty string")
        if (
            not keys
            or len(set(keys)) != len(keys)
            or any(not key.strip() for key in keys)
        ):
            raise ValueError("Key pool contains duplicate or empty keys")
        self.pool, self.redis, self.config = pool, redis, config
        self._keys = {hashlib.sha256(key.encode()).hexdigest(): key for key in keys}
        self._ids = sorted(self._keys)
        # Hash the name so punctuation (including ':') and Unicode cannot
        # overlap with Redis key separators or another pool's namespace.
        self._prefix = f"crkeys:pool:{hashlib.sha256(pool.encode()).hexdigest()}:"
        # NOTE Docker generates one boot ID before starting Python. All workers
        # in that container inherit it; on the next launch, even an unchanged
        # set of keys gets tested again. Replicas must share an explicit ID.
        generation = os.getenv("CR_API_BOOT_ID", "")
        self._config_id = hashlib.sha256(
            ("|".join(self._ids) + repr(config) + generation).encode()
        ).hexdigest()
        self._validator_task: asyncio.Task | None = None
        self._probe: Callable[[str], Awaitable[str]] | None = None

    def _name(self, suffix: str) -> str:
        return self._prefix + suffix

    def _validation_lock_ttl(self) -> int:
        # Background maintenance can probe once before checking every unknown key.
        return math.ceil(
            VALIDATION_LOCK_MARGIN_S
            + self.config.probe_timeout_s * (len(self._ids) + 1)
        )

    async def _probe_with_timeout(self, key: str) -> str:
        try:
            async with asyncio.timeout(self.config.probe_timeout_s):
                return await self._probe(key)
        except TimeoutError:
            return "temporary"

    async def initialize(
        self, probe: Callable[[str], Awaitable[str]]
    ) -> dict[str, int]:
        """Register this inventory and test each key once per boot generation.

        One worker obtains the validation lock. Others wait for its result
        instead of sending another request per key to Clash Royale. A lock TTL
        covers a failed worker; unknown keys are retried in the background.
        """
        self._probe = probe
        try:
            await self._register()
            lock = self._name("validation-lock")
            owner = uuid.uuid4().hex
            lock_ttl = self._validation_lock_ttl()
            # NX elects one validator across workers. The TTL recovers if it
            # dies; UNLOCK checks owner so a late worker cannot clear a new lock.
            if await self.redis.set(lock, owner, nx=True, ex=lock_ttl):
                try:
                    await self._validate_unknown()
                finally:
                    await self.redis.eval(UNLOCK, 1, lock, owner)
            else:
                for _ in range(lock_ttl * 4):
                    if not await self.redis.exists(lock):
                        break
                    await asyncio.sleep(0.25)
            counts = await self.inventory()
            self._validator_task = asyncio.create_task(self._validation_loop())
            return counts
        except RedisError as exc:
            raise KeyStoreUnavailable("Key store Redis unavailable") from exc

    async def _register(self):
        """Register this inventory in Redis (see REGISTER).

        Raises:
            ValueError: If a key is already registered to another pool.
            RedisError: If Redis is unavailable.
        """
        registry_keys = [
            self._name("config"),
            "crkeys:owners",
            self._name("ids"),
            self._name("status"),
            self._name("next"),
            self._name("cooldown"),
            self._name("last"),
            self._name("429-count"),
            self._name("sequence"),
            self._name("pool-next"),
        ]
        result = await self.redis.eval(
            REGISTER,
            len(registry_keys),
            *registry_keys,
            self._config_id,
            self.pool,
            *self._ids,
        )
        if result != "ok":
            raise ValueError(f"Key store {self.pool}: {result}")

    async def _validate_unknown(self):
        """Test keys without a conclusive usable/invalid result yet."""
        for key_id in self._ids:
            # A replaced configuration may have started while this HTTP probe
            # was in flight. It owns the new inventory, so stop writing this one.
            if await self.redis.get(self._name("config")) != self._config_id:
                return
            if await self.redis.hget(self._name("status"), key_id):
                continue
            if await self.redis.exists("crkeys:maintenance:next"):
                # Startup must not send one probe per key during maintenance.
                break
            status = await self._probe_with_timeout(self._keys[key_id])
            if await self.redis.get(self._name("config")) != self._config_id:
                return
            await self.redis.hset(
                self._name("next"),
                key_id,
                int(time.time() * 1000 + 1000 / self.config.requests_per_second),
            )
            # The validation request consumed this key's request interval.
            if status == "maintenance":
                await self._open_maintenance()
                break
            if status == "rate_limited":
                await self.redis.hset(
                    self._name("cooldown"),
                    key_id,
                    int(time.time() * 1000 + self.config.cooldown_initial_s * 1000),
                )
            elif status in ("usable", "invalid"):
                await self.redis.hset(self._name("status"), key_id, status)
            # 429 and network failures do not prove a key is invalid. Leave
            # them unknown so the background validator can retry later.

    async def _validation_loop(self):
        """Retry unknown keys and run sparse, coordinated maintenance probes.

        A single global probe lock is shared by app and scraper because
        maintenance describes the upstream service, not a particular pool.
        The per-pool validation lock handles ordinary temporary key failures.
        """
        while True:
            await asyncio.sleep(max(5.0, self.config.maintenance_probe_initial_s))
            try:
                config_id = await self.redis.get(self._name("config"))
                if config_id is None:
                    # Redis lost the pool state (volume removed, FLUSHALL).
                    # Every acquire fails closed until the inventory exists
                    # again, and nothing else would recreate it in a running
                    # process. Workers of this configuration register the same
                    # ID, so the first one wins and the rest change nothing.
                    # The keys are unknown again and get validated below.
                    logger.warning(
                        "Key store %s lost its Redis state, registering again",
                        self.pool,
                    )
                    await self._register()
                elif config_id != self._config_id:
                    # A newer deployment owns the pool. Taking it back would
                    # make the two configurations replace each other.
                    return
                if await self._maintenance_delay_ms() > 0:
                    continue
                owner = uuid.uuid4().hex
                # A maintenance probe has a global lock; ordinary validation
                # only needs to exclude another worker in this same pool.
                lock_name = (
                    "crkeys:probe-lock"
                    if await self.redis.exists("crkeys:maintenance:next")
                    else self._name("validation-lock")
                )
                if await self.redis.set(
                    lock_name, owner, nx=True, ex=self._validation_lock_ttl()
                ):
                    try:
                        if await self.redis.get("crkeys:maintenance:next"):
                            statuses = await self.redis.hgetall(self._name("status"))
                            # A key known to be rejected cannot show whether
                            # maintenance ended, so choose a usable/unknown one.
                            probe_id = next(
                                (
                                    key_id
                                    for key_id in self._ids
                                    if statuses.get(key_id) != "invalid"
                                ),
                                None,
                            )
                            if probe_id is None:
                                continue
                            status = await self._probe_with_timeout(
                                self._keys[probe_id]
                            )
                            if (
                                await self.redis.get(self._name("config"))
                                != self._config_id
                            ):
                                return
                            await self.redis.hset(
                                self._name("next"),
                                probe_id,
                                int(
                                    time.time() * 1000
                                    + 1000 / self.config.requests_per_second
                                ),
                            )
                            if status == "maintenance":
                                await self._open_maintenance()
                            elif status == "usable":
                                # Only a successful probe closes the circuit;
                                # a generic 5xx/network error is inconclusive.
                                await self.redis.hset(
                                    self._name("status"), probe_id, "usable"
                                )
                                await self.redis.delete(
                                    "crkeys:maintenance:next",
                                    "crkeys:maintenance:count",
                                )
                            elif status == "invalid":
                                await self.redis.hset(
                                    self._name("status"), probe_id, "invalid"
                                )
                            else:
                                await self._open_maintenance()
                        await self._validate_unknown()
                    finally:
                        await self.redis.eval(UNLOCK, 1, lock_name, owner)
            except Exception:
                # A later pass retries. Ordinary acquisition still reads Redis
                # and fails closed if coordination itself is unavailable.
                continue

    async def _open_maintenance(self):
        """Space later probes further apart while maintenance continues."""
        count = await self.redis.incr("crkeys:maintenance:count")
        delay = min(
            self.config.maintenance_probe_max_s,
            self.config.maintenance_probe_initial_s * 2 ** min(count - 1, 12),
        )
        await self.redis.set(
            "crkeys:maintenance:next", int(time.time() * 1000 + delay * 1000)
        )

    async def _maintenance_delay_ms(self) -> int:
        next_at = await self.redis.get("crkeys:maintenance:next")
        return max(0, int(next_at) - int(time.time() * 1000)) if next_at else 0

    async def in_maintenance(self) -> bool:
        try:
            return bool(await self.redis.exists("crkeys:maintenance:next"))
        except RedisError as exc:
            raise KeyStoreUnavailable("Key store Redis unavailable") from exc

    async def inventory(self) -> dict[str, int]:
        """Report validity counts, not raw keys or instantaneous free slots."""
        try:
            statuses = await self.redis.hgetall(self._name("status"))
        except RedisError as exc:
            raise KeyStoreUnavailable("Key store Redis unavailable") from exc
        return {
            "configured": len(self._ids),
            "usable": sum(statuses.get(key_id) == "usable" for key_id in self._ids),
            "invalid": sum(statuses.get(key_id) == "invalid" for key_id in self._ids),
        }

    async def try_acquire(self) -> tuple[KeyLease | None, float]:
        """Atomically claim the least recently used eligible key, if any.

        The returned delay is only a hint. Another worker may release a key
        earlier, so callers retry within their configured acquisition budget.
        """
        owner = uuid.uuid4().hex
        try:
            keys = [
                self._name("status"),
                self._name("next"),
                self._name("cooldown"),
                self._name("last"),
                self._name("pool-next"),
                "crkeys:maintenance:next",
                self._name("lease:"),
                self._name("sequence"),
                self._name("config"),
            ]
            # eval takes the number of Redis KEYS, then those KEYS, then ARGV.
            # ARGV is owner, lease TTL (ms), every key ID, per-key interval
            # (ms), pool interval (ms; zero disables it), and configuration ID.
            # ACQUIRE returns a 1-based index into self._ids or zero plus a
            # suggested wait in ms; -1 means inventory changed or was lost.
            selected, wait_ms = await self.redis.eval(
                ACQUIRE,
                len(keys),
                *keys,
                owner,
                math.ceil(self.config.lease_s * 1000),
                *self._ids,
                math.ceil(1000 / self.config.requests_per_second),
                (
                    math.ceil(1000 / self.config.pool_requests_per_second)
                    if self.config.pool_requests_per_second
                    else 0
                ),
                self._config_id,
            )
            if int(selected) < 0:
                # Redis lost state or another boot replaced this worker's pool.
                raise KeyStoreUnavailable(
                    "Key inventory configuration changed or was lost"
                )
            if selected:
                key_id = self._ids[int(selected) - 1]
                return KeyLease(self._keys[key_id], key_id, owner), 0.0
            # The Lua wait may be zero when all usable keys are merely leased.
            # Keep a minimum retry interval so callers do not spin on Redis.
            return None, max(self.config.acquisition_backoff_s, int(wait_ms) / 1000)
        except RedisError as exc:
            raise KeyStoreUnavailable("Key store Redis unavailable") from exc

    async def acquire(self, deadline: float | None = None) -> KeyLease:
        """Wait briefly for a key, then give the caller an explicit outcome.

        The client's overall deadline also covers HTTP retries. Without it,
        each 429 could start another full sequence of acquisition attempts.
        """
        retry_after = self.config.acquisition_backoff_s
        loop = asyncio.get_running_loop()
        for attempt in range(self.config.acquisition_attempts):
            if deadline is not None and loop.time() >= deadline:
                break
            lease, retry_after = await self.try_acquire()
            if lease:
                return lease
            if attempt + 1 < self.config.acquisition_attempts:
                wait = min(retry_after, 1.0)
                if deadline is not None:
                    wait = min(wait, max(0, deadline - loop.time()))
                await asyncio.sleep(wait)
        try:
            if await self.redis.exists("crkeys:maintenance:next"):
                raise NoKeyAvailable(
                    max(retry_after, await self._maintenance_delay_ms() / 1000),
                    "maintenance",
                )
        except RedisError as exc:
            raise KeyStoreUnavailable("Key store Redis unavailable") from exc
        counts = await self.inventory()
        # Exhaustion, failed validation, and bad configuration need different
        # results even though none can hand out a key right now.
        if counts["usable"] == 0:
            reason = (
                "no_valid_keys"
                if counts["invalid"] == counts["configured"]
                else "validation_pending"
            )
            raise NoKeyAvailable(retry_after, reason)
        raise NoKeyAvailable(retry_after)

    async def release(self, lease: KeyLease, outcome: str, retry_after_s: float = 0):
        """Record the response and release only the lease owned by this job.

        Redis applies cooldown/maintenance state before freeing the key.
        Retry-After is capped by configuration so a bad header cannot disable
        a key indefinitely; 429s without it use growing per-key backoff.
        """
        try:
            # KEYS name the lease and shared outcome records. ARGV supplies
            # owner, key ID, outcome, delay bounds (ms), and configuration ID.
            # RELEASE checks lease ownership before changing any shared state.
            await self.redis.eval(
                RELEASE,
                7,
                self._name("lease:") + lease.key_id,
                self._name("status"),
                self._name("cooldown"),
                self._name("429-count"),
                "crkeys:maintenance:next",
                "crkeys:maintenance:count",
                self._name("config"),
                lease.owner,
                lease.key_id,
                outcome,
                math.ceil(
                    self.config.cooldown_initial_s * 1000 * random.uniform(0.9, 1.1)
                ),
                math.ceil(self.config.cooldown_max_s * 1000),
                math.ceil(min(retry_after_s, self.config.retry_after_max_s) * 1000),
                math.ceil(self.config.maintenance_probe_initial_s * 1000),
                math.ceil(self.config.maintenance_probe_max_s * 1000),
                self._config_id,
            )
        except RedisError as exc:
            raise KeyStoreUnavailable("Could not release key lease") from exc

    async def close(self):
        if self._validator_task:
            self._validator_task.cancel()
            # gather returns the validator's own cancellation as a result.
            # Awaiting the task directly would raise it, ending the caller's
            # shutdown before its other connections are closed.
            await asyncio.gather(self._validator_task, return_exceptions=True)
        await self.redis.aclose()
