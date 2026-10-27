"""Per-player scraping schedule shared by the data scraper and the API.

Each schedule is one Redis sorted set: member = player tag, score = the time
(epoch ms) at which the player is due. A sorted set holds every member at most
once, so a player can never be queued twice and the schedule can never grow
past the number of tracked players. When workers fall behind, players become
late instead of the queue growing.

A claim moves the player's score to the end of the claim TTL and records an
owner token. If the worker dies, the claim simply expires and the player is
due again, without a separate recovery path. An ack only applies while the
owner still matches, so a late ack cannot overwrite a newer claim.

MongoDB stays the source of truth for which players are tracked. The schedule
can be rebuilt from it at any time (see the scraper's reconciler), so losing
this Redis data only delays scraping until the next reconciliation.
"""

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

# KEYS are the schedule ZSET and its owner hash. ARGV is the claim limit, the
# claim TTL (ms), the owner token, and the minimum lateness (ms) a player needs
# to be claimed. Selecting and moving the due players has to be one Redis
# operation; otherwise two workers could read the same due player before
# either moved it and both would process it.
CLAIM = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local latest_due = now - tonumber(ARGV[4])
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', latest_due, 'WITHSCORES', 'LIMIT', 0, tonumber(ARGV[1]))
local claimed = {}
-- WITHSCORES returns member, score pairs.
for i = 1, #due, 2 do
    redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[2]), due[i])
    redis.call('HSET', KEYS[2], due[i], ARGV[3])
    table.insert(claimed, due[i])
    -- Lateness is reported to the caller for monitoring. Score 0 marks a
    -- newly added player, which was never late.
    local score = tonumber(due[i + 1])
    table.insert(claimed, tostring(score > 0 and now - score or 0))
end
return claimed
"""

# KEYS are the schedule ZSET and its owner hash. ARGV is the player tag, the
# owner token, and the delay (ms) until the player is due again. The delay is
# applied to Redis time so every worker shares one clock. XX keeps a player
# that was untracked during its claim from being added back.
ACK = """
if redis.call('HGET', KEYS[2], ARGV[1]) ~= ARGV[2] then return 0 end
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[3]), ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
return 1
"""


# KEYS are the schedule ZSET and its owner hash. ARGV holds player tag and
# due time (ms) pairs. A claimed player keeps its score, which is its claim's
# expiry: an earlier one would let a second worker claim the player while the
# first still syncs it. One script, so no claim can start between the check
# and the write.
RESCHEDULE_UNCLAIMED = """
local changed = 0
for i = 1, #ARGV, 2 do
    if redis.call('HEXISTS', KEYS[2], ARGV[i]) == 0 then
        redis.call('ZADD', KEYS[1], tonumber(ARGV[i + 1]), ARGV[i])
        changed = changed + 1
    end
end
return changed
"""

# Players per RESCHEDULE_UNCLAIMED call, so one script never blocks Redis for
# long.
RESCHEDULE_BATCH = 500


@dataclass(frozen=True)
class Claim:
    """One claimed player and the owner token required to acknowledge it."""

    player_tag: str
    owner: str
    lateness_s: float


class Schedule:
    """One named per-player schedule in the scheduling Redis.

    Args:
        redis (Redis): Client for the non-evicting scheduling Redis
            (redis-key-store), created with ``decode_responses=True``.
        name (str): Schedule name, e.g. ``"battles"``.
    """

    def __init__(self, redis: Redis, name: str):
        self.redis = redis
        self.name = name
        self._zset = f"crsched:{name}"
        self._owners = f"crsched:{name}:owner"

    async def add(self, player_tag: str, due_ms: int = 0, only_new: bool = False):
        """Schedule a player at an absolute due time.

        Args:
            player_tag (str): Player tag (e.g., "#YYRJQY28").
            due_ms (int): Epoch milliseconds at which the player is due. Zero
                places the player in front of every other due player.
            only_new (bool): Keep the existing due time of an already scheduled
                player instead of replacing it.
        """

        await self.redis.zadd(self._zset, {player_tag: due_ms}, nx=only_new)

    async def add_many(self, due_by_tag: dict[str, int]):
        """Schedule several players without touching already scheduled ones."""

        if due_by_tag:
            await self.redis.zadd(self._zset, due_by_tag, nx=True)

    async def reschedule_unclaimed(self, due_by_tag: dict[str, int]) -> int:
        """Schedule players at absolute due times, unless a worker claimed them.

        Replaces existing due times, e.g. the due time 0 the reconciler gives
        players inserted moments before, but never a running claim.

        Args:
            due_by_tag (dict[str, int]): Player tag mapped to epoch milliseconds.

        Returns:
            int: Number of players scheduled; the rest were claimed.
        """

        items = list(due_by_tag.items())
        changed = 0
        for start in range(0, len(items), RESCHEDULE_BATCH):
            args = [
                value
                for tag, due_ms in items[start : start + RESCHEDULE_BATCH]
                for value in (tag, due_ms)
            ]
            changed += await self.redis.eval(
                RESCHEDULE_UNCLAIMED, 2, self._zset, self._owners, *args
            )
        return changed

    async def remove(self, *player_tags: str):
        """Remove players from the schedule, including a running claim."""

        if not player_tags:
            return
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zrem(self._zset, *player_tags)
            pipe.hdel(self._owners, *player_tags)
            await pipe.execute()

    async def claim(
        self, limit: int, claim_ttl_s: float, min_lateness_s: float = 0
    ) -> list[Claim]:
        """Atomically claim up to ``limit`` due players.

        Args:
            limit (int): Maximum number of players to claim.
            claim_ttl_s (float): Seconds after which an unacknowledged claim
                expires and the player is due again. Has to exceed the longest
                job duration, otherwise a slow job is processed twice.
            min_lateness_s (float): Only claim players that have been due for
                at least this long. Lets a low-priority schedule jump ahead
                only once its players are overdue by more than a grace period.

        Returns:
            list[Claim]: Claimed players, most overdue first. Empty if nothing
            is due.
        """

        owner = uuid.uuid4().hex
        result = await self.redis.eval(
            CLAIM,
            2,
            self._zset,
            self._owners,
            limit,
            int(claim_ttl_s * 1000),
            owner,
            int(min_lateness_s * 1000),
        )
        return [
            Claim(result[i], owner, max(0.0, int(result[i + 1]) / 1000))
            for i in range(0, len(result), 2)
        ]

    async def ack(self, claim: Claim, delay_s: float) -> bool:
        """Finish a claim and schedule the player again after ``delay_s``.

        Returns:
            bool: False if the claim had already expired and another worker
            owns the player now, or the player was removed meanwhile.
        """

        result = await self.redis.eval(
            ACK,
            2,
            self._zset,
            self._owners,
            claim.player_tag,
            claim.owner,
            int(delay_s * 1000),
        )
        return bool(result)

    async def scheduled_tags(self) -> set[str]:
        """Return every scheduled player tag, iterating in batches."""

        tags = set()
        async for tag, _score in self.redis.zscan_iter(self._zset, count=1000):
            tags.add(tag)
        return tags

    async def seconds_until_next_due(self) -> float | None:
        """Seconds until the earliest player is due; None if nothing is scheduled."""

        first = await self.redis.zrange(self._zset, 0, 0, withscores=True)
        if not first:
            return None
        _tag, score = first[0]
        return max(0.0, score / 1000 - await self._redis_now_s())

    async def stats(self) -> dict:
        """Return the scheduled and due player counts and the current lateness.

        Returns:
            dict: scheduled, due (due and not claimed yet), and
            oldest_due_lateness_s, the seconds the most overdue unclaimed
            player has been waiting. A lateness that keeps growing means the
            workers cannot keep up.
        """

        now_ms = int(await self._redis_now_s() * 1000)
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.zcard(self._zset)
            pipe.zcount(self._zset, "-inf", now_ms)
            # Skip score 0 (players added for an immediate first sync): they
            # were never scheduled at a real time, so they have no lateness.
            pipe.zrangebyscore(self._zset, 1, now_ms, start=0, num=1, withscores=True)
            scheduled, due, oldest = await pipe.execute()
        lateness = (now_ms - oldest[0][1]) / 1000 if oldest else 0.0
        return {"scheduled": scheduled, "due": due, "oldest_due_lateness_s": lateness}

    async def _redis_now_s(self) -> float:
        seconds, microseconds = await self.redis.time()
        return seconds + microseconds / 1_000_000
