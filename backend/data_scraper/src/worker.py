"""Worker pool that continuously processes due players from the schedules.

Each worker claims one due player, runs its job, and acknowledges it with the
delay until the player is due again. There is no batch cycle: players are
processed whenever they become due, and a slow or failing player only delays
itself.

Battle syncs take priority because a late battle sync can lose battles for
good, while a late profile is only stale. Profiles run when no battle sync is
due, or, on one worker, once they are overdue by more than PROFILE_MAX_LATENESS.
"""

import asyncio
import logging
import math
import time

from clash_royale_api import ClashRoyaleAPI
from game_modes import UniqueGameModes
from intervals import failure_backoff
from jobs.battles import sync_player_battles
from jobs.common import JobResult
from jobs.profiles import refresh_player_profile
from metrics import Metrics
from mongo import MongoConn
from scrape_schedule import Capacity, Claim, Schedule
from settings import settings

logger = logging.getLogger(__name__)


class WorkerPool:
    """Runs a resizable number of workers in this process.

    Claims are atomic in Redis, so several scraper processes can run the same
    pool against one schedule without processing a player twice.
    """

    def __init__(
        self,
        battles: Schedule,
        profiles: Schedule,
        cr_api: ClashRoyaleAPI,
        mongo_conn: MongoConn,
        mode_store: UniqueGameModes,
        metrics: Metrics,
    ):
        self.battles = battles
        self.profiles = profiles
        self.cr_api = cr_api
        self.mongo_conn = mongo_conn
        self.mode_store = mode_store
        self.metrics = metrics
        self.target = 0
        # Until the first capacity estimate, sync at the shortest interval
        self.base_interval_s = settings.MIN_SYNC_INTERVAL
        self._workers: dict[int, asyncio.Task] = {}

    def resize(self, capacity: Capacity):
        """Apply a new capacity estimate: base interval and worker count.

        Workers above the new target exit after their current player, so a
        shrinking pool never abandons a claimed player.
        """

        self.base_interval_s = capacity.base_interval_s

        # The key store limits how fast requests start; workers only decide
        # how many players can be in flight at once. A job spends most of its
        # time waiting on the HTTP response and Mongo, so several workers per
        # request/s are needed to start a new request every time a key frees
        # up. Extra workers are cheap: they wait inside the key store's acquire.
        wanted = math.ceil(
            capacity.usable_keys
            * settings.CR_KEY_REQUESTS_PER_SECOND
            * settings.WORKERS_PER_KEY_REQUEST_PER_SECOND
        )
        target = max(settings.MIN_WORKERS, min(settings.MAX_WORKERS, wanted))
        if target != self.target:
            logger.info(
                "Worker target %d -> %d (%d usable keys)",
                self.target,
                target,
                capacity.usable_keys,
            )
        self.target = target

        # Start missing workers. Finished tasks are replaced as well, so a
        # worker that died from an unexpected error comes back.
        for index in range(target):
            task = self._workers.get(index)
            if task is None or task.done():
                self._workers[index] = asyncio.create_task(
                    self._run(index), name=f"scrape-worker-{index}"
                )

    async def stop(self):
        """Cancel all workers. Each hands its claimed player back as due immediately."""

        for task in self._workers.values():
            task.cancel()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()

    async def _claim_next(self, index: int) -> tuple[Schedule, Claim] | None:
        """Claim the next player by priority, or None if nothing is due."""

        # 1. Profiles overdue beyond the grace period, so a constantly busy
        #    pool cannot starve profile refreshes forever. Only worker 0 puts
        #    them first: after a long outage every profile is overdue, and
        #    with all workers on that backlog, due battle syncs would wait
        #    until it is gone and could lose battles.
        # 2. Due battle syncs.
        # 3. Due profiles, using capacity the battle syncs leave unused.
        order = [(self.battles, 0), (self.profiles, 0)]
        if index == 0:
            order.insert(0, (self.profiles, settings.PROFILE_MAX_LATENESS))
        for schedule, min_lateness_s in order:
            claims = await schedule.claim(1, settings.CLAIM_TTL, min_lateness_s)
            if claims:
                return schedule, claims[0]
        return None

    async def _idle_wait(self) -> float:
        """Seconds to sleep when nothing is due.

        Until the next player is due, but at most the idle poll interval: the
        API can add a player due immediately at any time. The lower bound
        avoids a busy loop when a player is due in a few milliseconds.
        """

        waits = [
            wait
            for wait in (
                await self.battles.seconds_until_next_due(),
                await self.profiles.seconds_until_next_due(),
            )
            if wait is not None
        ]
        wait = min(waits) if waits else settings.IDLE_POLL_INTERVAL
        return min(max(wait, 0.1), settings.IDLE_POLL_INTERVAL)

    async def _run(self, index: int):
        # Indexes are stable, so lowering the target stops exactly the
        # highest-numbered workers once they finish their current player.
        while index < self.target:
            try:
                if await self.cr_api.key_store.in_maintenance():
                    # Claiming now would only push every player back by the
                    # maintenance delay; the key store probes for recovery.
                    await asyncio.sleep(settings.MAINTENANCE_RETRY_DELAY)
                    continue

                # One player per claim: a batch would hold claims on players
                # that other, idle workers could already be processing.
                claimed = await self._claim_next(index)
                if claimed is None:
                    await asyncio.sleep(await self._idle_wait())
                    continue

                await self._process(*claimed)

            except asyncio.CancelledError:
                raise
            except Exception:
                # Redis or Mongo trouble outside a single player's job. Back
                # off instead of spinning; claims expire on their own.
                logger.exception("Worker %d error", index)
                await asyncio.sleep(settings.IDLE_POLL_INTERVAL)

    async def _run_job(self, schedule: Schedule, claim: Claim) -> JobResult:
        if schedule is self.battles:
            return await sync_player_battles(
                claim.player_tag,
                self.cr_api,
                self.mongo_conn,
                self.mode_store,
                self.base_interval_s,
                claim.lateness_s,
            )
        return await refresh_player_profile(
            claim.player_tag, self.cr_api, self.mongo_conn, self.base_interval_s
        )

    async def _process(self, schedule: Schedule, claim: Claim):
        started = time.monotonic()
        try:
            async with asyncio.timeout(settings.JOB_TIMEOUT):
                result = await self._run_job(schedule, claim)
        except TimeoutError:
            # Mostly a hung Mongo operation. A battle sync cut off between its
            # insert and its state write is repaired by the next sync, which
            # counts the battles past the watermark again.
            logger.warning(
                "%s job of %s timed out",
                schedule.name.capitalize(),
                claim.player_tag,
            )
            result = JobResult("failed", failure_backoff(1))
        except asyncio.CancelledError:
            # Shutdown: hand the player back as due now instead of leaving it
            # blocked until the claim expires. Shielded, so the ack finishes
            # although this task is being cancelled.
            await asyncio.shield(schedule.ack(claim, 0))
            raise
        except Exception:
            # Unexpected errors (mostly Mongo) are retried after the base
            # backoff without a failure count: the cause is not the player.
            logger.exception(
                "%s job of %s failed", schedule.name.capitalize(), claim.player_tag
            )
            result = JobResult("failed", failure_backoff(1))

        self.metrics.record(
            schedule.name,
            result.outcome,
            result.inserted,
            lateness_s=claim.lateness_s,
            duration_s=time.monotonic() - started,
            possible_gap=result.possible_gap,
        )

        if result.outcome in ("inactive", "deactivated"):
            # Gone from both schedules, whichever job noticed it
            await self.battles.remove(claim.player_tag)
            await self.profiles.remove(claim.player_tag)
            return

        if not await schedule.ack(claim, result.delay_s):
            # The claim expired during the job and another worker owns the
            # player now, or it was untracked meanwhile. Battle inserts and
            # the $max watermark are safe to repeat. Other player fields
            # (interval, name, profile) keep whichever job wrote last, which
            # is at most one sync old and corrected by the next one.
            logger.warning(
                "Claim of %s was no longer owned at its ack (expired or untracked)",
                claim.player_tag,
            )
