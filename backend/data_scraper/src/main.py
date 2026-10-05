"""Data scraper entry point: connects services and runs the background loops.

Battle syncs and profile refreshes run continuously per player (see worker.py).
The other loops run on their own timers: reconciliation with capacity planning,
game mode flushes, card refreshes, and monitoring.
"""

import asyncio
import contextlib
import logging
import signal
import time
from functools import partial

from redis.asyncio import Redis

from api_key_store import KeyStore, KeyStoreConfig, keys_from_env
from clash_royale_api import ClashRoyaleAPI
from game_modes import UniqueGameModes
from intervals import battle_demand, min_profile_interval, profile_demand
from jobs.cards import cards_loop
from log import setup_logging
from metrics import (
    Metrics,
    append_history,
    build_history_sample,
    build_snapshot,
    publish_snapshot,
    start_status_server,
)
from mongo import (
    MongoConn,
    get_database_health,
    get_game_modes,
    insert_game_modes,
)
from reconciler import reconcile_schedules
from redis_service import GAME_MODES_CACHE_KEY, CacheRedisConn, set_redis_json
from scrape_schedule import (
    BATTLES_SCHEDULE,
    PROFILES_SCHEDULE,
    Schedule,
    compute_capacity,
    publish_capacity,
)
from settings import settings
from worker import WorkerPool

logger = logging.getLogger("scraper")


async def init():
    """Initialize API, Redis, and MongoDB clients. Does a health/connection check.

    Returns:
        tuple[ClashRoyaleAPI, Redis, CacheRedisConn, MongoConn]: Initialized
        clients. The plain Redis client is the key store/scheduling Redis.

    Raises:
        SystemExit: If any connection fails after all retries.
    """

    # Use the same dedicated Redis server as the API app. The scraper's pool
    # still has its own tokens, request interval, and cooldown history.
    key_redis = Redis(
        host=settings.KEY_STORE_REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
        # Without timeouts a hung Redis blocks claims, acks, and the shielded
        # shutdown acks indefinitely. Matches the cache connection.
        socket_connect_timeout=3,
        socket_timeout=5,
    )
    await retry_async(key_redis.ping, name="key store Redis")
    key_store = KeyStore(
        "scraper",
        keys_from_env("scraper"),
        key_redis,
        KeyStoreConfig(
            requests_per_second=settings.CR_KEY_REQUESTS_PER_SECOND,
            pool_requests_per_second=settings.CR_KEY_POOL_REQUESTS_PER_SECOND,
        ),
    )
    cr_api = ClashRoyaleAPI(key_store=key_store)
    # The probes identify bad tokens before the scraping loop starts. Unknown
    # keys from transient failures are retried later by the store.
    inventory = await retry_async(
        cr_api.check_connection, name="Clash Royale key inventory"
    )
    logger.info("Scraper Clash Royale keys: %s", inventory)

    # Retry Redis
    redis_conn = CacheRedisConn(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
    )
    await retry_async(redis_conn.connect, name="Redis")

    # Retry MongoDB
    mongo_conn = MongoConn(app_name=settings.MONGO_CLIENT_NAME)
    await retry_async(mongo_conn.connect, name="MongoDB")

    logger.info("Successfully connected to all services")
    return cr_api, key_redis, redis_conn, mongo_conn


async def retry_async(func, name):
    """
    Retry an asynchronous connection or operation multiple times with delay.

    This function attempts to execute the provided asynchronous `func` up to
    `settings.INIT_RETRIES` times. If it fails, it waits for
    `settings.INIT_RETRY_DELAY` seconds between attempts. On success, it
    returns the result of `func`. If all retries fail, the process exits
    with status code 1.

    Args:
        func (Callable[[], Awaitable]): An asynchronous function (e.g., `redis.connect`) that will be retried.
        name (str): A readable identifier for logging (e.g., "Redis", "MongoDB").

    Returns:
        Any: The result of the successfully awaited `func`.

    Raises:
        SystemExit: If all retries are exhausted without success.
    """

    retries = settings.INIT_RETRIES
    delay = settings.INIT_RETRY_DELAY

    for attempt in range(1, retries + 1):
        try:
            return await func()
        except Exception:
            logger.exception(
                "Failed to connect to %s (attempt %d/%d)", name, attempt, retries
            )
            if attempt < retries:
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Exiting after %d failed attempts to connect to %s", retries, name
                )
                exit(1)


async def reconcile_loop(
    battles: Schedule,
    profiles: Schedule,
    mongo_conn: MongoConn,
    pool: WorkerPool,
    metrics: Metrics,
):
    """Reconcile the schedules, plan capacity, and resize the pool until cancelled.

    Capacity follows the usable keys, which change when keys fail validation,
    get rejected, or recover, and the tracked players and their activity. The
    estimate sets the base battle interval, the worker count, and, through
    Redis, the API's admission limit for new players.
    """

    key_store = pool.cr_api.key_store
    while True:
        try:
            tracked = await reconcile_schedules(battles, profiles, mongo_conn)
            players = list(tracked.values())
            inventory = await key_store.inventory()
            capacity = compute_capacity(
                active_players=len(players),
                usable_keys=inventory["usable"],
                per_key_rps=settings.CR_KEY_REQUESTS_PER_SECOND,
                utilization=settings.CAPACITY_UTILIZATION,
                # Admission assumes full load, where profiles are stretched
                profile_max_age_s=min_profile_interval(settings.MAX_SYNC_INTERVAL),
                min_interval_s=settings.MIN_SYNC_INTERVAL,
                max_interval_s=settings.MAX_SYNC_INTERVAL,
                # partial binds this pass's players; both are called right away
                battle_demand=partial(battle_demand, players),
                profile_demand=partial(profile_demand, players),
            )
            await publish_capacity(key_store.redis, capacity)
            pool.resize(capacity)
            metrics.capacity = capacity
            metrics.last_reconcile_at = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The next pass retries; workers keep running on the current schedule.
            logger.exception("Schedule reconciliation failed")
        await asyncio.sleep(settings.RECONCILE_INTERVAL)


async def store_game_modes(game_modes: list, mongo_conn: MongoConn) -> bool:
    """Write game modes to Mongo and report whether any of them is new.

    Args:
        game_modes (list): Unique game mode names seen since the last flush.
        mongo_conn (MongoConn): Active Mongo connection.

    Returns:
        bool: True if at least one mode was not stored before.

    Raises:
        Exception: If the Mongo write fails. The caller keeps the modes for a retry.
    """

    result = await insert_game_modes(mongo_conn, game_modes)
    if not result.get("inserted"):
        return False
    logger.info("%d new game modes inserted", result["inserted"])
    return True


async def refresh_game_modes_cache(mongo_conn: MongoConn, redis_conn: CacheRedisConn):
    """Rebuild the API's cached game mode list from Mongo.

    Raises:
        Exception: Any Mongo or Redis error; the caller retries on the next flush.
    """

    game_modes = await get_game_modes(mongo_conn)
    await set_redis_json(
        redis_conn,
        GAME_MODES_CACHE_KEY,
        game_modes,
        ttl=settings.CACHE_TTL_GAME_MODES,
    )


async def game_modes_loop(
    mode_store: UniqueGameModes, mongo_conn: MongoConn, redis_conn: CacheRedisConn
):
    """Write newly seen game modes to Mongo and keep the cached list current.

    This loop is the only writer of GAME_MODES_CACHE_KEY. The API reads it and
    falls back to Mongo without caching, because a cache write from a request
    that read Mongo before a new mode was inserted could land after this loop's
    write and hide the new mode for a full TTL. Writes from this single task are
    ordered, so the cached list never goes back to an older state.
    """

    # Zero builds the cached list on the first pass
    next_cache_refresh = 0.0
    while True:
        new_mode = False
        game_modes = mode_store.drain()
        if game_modes:
            try:
                new_mode = await store_game_modes(game_modes, mongo_conn)
            except Exception:
                # Keep the modes for the next flush instead of dropping them.
                mode_store.restore(game_modes)
                # The failed write may still have inserted a new mode. The
                # retry would then not count it as new, so the cache is
                # rebuilt on the next pass regardless.
                next_cache_refresh = 0.0
                logger.exception("Game mode flush failed")

        # A new mode is written to the cache right away, so the filter options
        # include it as soon as its battles can show up. The timed refresh
        # restores the list after an eviction or a restart of redis-cache.
        if new_mode or time.monotonic() >= next_cache_refresh:
            try:
                await refresh_game_modes_cache(mongo_conn, redis_conn)
                next_cache_refresh = (
                    time.monotonic() + settings.GAME_MODES_CACHE_REFRESH_INTERVAL
                )
            except Exception:
                # Zero retries on the next flush, which also covers a new mode
                # that is already in Mongo and would not count as new again.
                next_cache_refresh = 0.0
                logger.exception("Game mode cache refresh failed")

        await asyncio.sleep(settings.GAME_MODES_FLUSH_INTERVAL)


async def monitoring_loop(
    metrics: Metrics,
    battles: Schedule,
    profiles: Schedule,
    pool: WorkerPool,
    mongo_conn: MongoConn,
):
    """Publish the snapshot, record history samples, and log until cancelled."""

    key_store = pool.cr_api.key_store
    last_log = time.monotonic()
    last_sample = time.monotonic()
    while True:
        await asyncio.sleep(settings.METRICS_INTERVAL)
        # Collected apart from the snapshot and bounded in time, so a Mongo
        # outage shows up as unreachable instead of stalling the Redis based
        # status until the driver's own timeouts expire.
        try:
            async with asyncio.timeout(settings.MONGO_HEALTH_TIMEOUT):
                mongo = {"ok": True, **await get_database_health(mongo_conn)}
        except Exception:
            mongo = {"ok": False}
            logger.warning("Mongo health check failed")
        try:
            snapshot = await build_snapshot(
                metrics, battles, profiles, key_store, pool.target, mongo
            )
            await publish_snapshot(metrics, key_store, snapshot)
        except Exception:
            logger.exception("Metrics snapshot failed")
            continue

        if time.monotonic() - last_sample >= settings.METRICS_HISTORY_INTERVAL:
            span_s = time.monotonic() - last_sample
            last_sample = time.monotonic()
            sample = build_history_sample(
                metrics.take_sample_counts(), snapshot, span_s
            )
            try:
                await append_history(key_store.redis, sample)
            except Exception:
                # The counts are gone, so the charts show a missing point
                # instead of a later one that counts two periods as one.
                logger.exception("Metrics history write failed")

        # The log line keeps a coarse history in the container logs, which
        # the snapshot (always only the latest state) does not.
        if time.monotonic() - last_log >= settings.STATUS_LOG_INTERVAL:
            last_log = time.monotonic()
            jobs = snapshot["jobs"]
            schedules = snapshot["schedules"]
            logger.info(
                "Last %ds: %d battle jobs %s, %d battles inserted, %d profile jobs | "
                "battles %d scheduled, %d due, oldest waiting %.0fs | %d workers",
                settings.METRICS_WINDOW,
                jobs["battles"]["jobs"],
                jobs["battles"]["outcomes"] or "{}",
                jobs["battles"]["inserted"],
                jobs["profiles"]["jobs"],
                schedules["battles"]["scheduled"],
                schedules["battles"]["due"],
                schedules["battles"]["oldest_due_lateness_s"],
                pool.target,
            )


async def main():
    """Start the scraper and run its loops until the process is stopped.

    - Waits for dependent services and validates the scraper key pool.
    - Rebuilds the schedules from the tracked players in Mongo.
    - Runs the workers, the timer loops, and the status endpoint concurrently.
    - On SIGTERM (docker stop) or SIGINT, hands claimed players back, flushes
      collected game modes, and closes all connections.
    """

    setup_logging()

    # Without a handler, SIGTERM ends Python immediately and none of the
    # cleanup below runs. Cancelling the main task unwinds it normally.
    # add_signal_handler is unavailable on Windows; the container runs Linux.
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, main_task.cancel)

    cr_api, key_redis, redis_conn, mongo_conn = await init()

    battles = Schedule(key_redis, BATTLES_SCHEDULE)
    profiles = Schedule(key_redis, PROFILES_SCHEDULE)
    mode_store = UniqueGameModes()
    metrics = Metrics()
    pool = WorkerPool(battles, profiles, cr_api, mongo_conn, mode_store, metrics)
    status_server = await start_status_server(metrics, key_redis)

    try:
        # reconcile_loop runs its first pass immediately, which also starts
        # the workers. Every loop handles its own errors, so the group only
        # ends on cancellation; Docker's restart policy covers anything else.
        # NOTE Claims let several scraper processes share the workers, but the
        # card and game mode loops are the only writers of their cache keys
        # (and the card loop of the card image volume) and have no leader
        # election. Run one scraper process, or move these
        # loops behind a Redis lock before scaling out.
        async with asyncio.TaskGroup() as tg:
            tg.create_task(reconcile_loop(battles, profiles, mongo_conn, pool, metrics))
            tg.create_task(game_modes_loop(mode_store, mongo_conn, redis_conn))
            tg.create_task(cards_loop(cr_api, mongo_conn, redis_conn))
            tg.create_task(
                monitoring_loop(metrics, battles, profiles, pool, mongo_conn)
            )
    except asyncio.CancelledError:
        logger.info("Stopping data scraper")
        raise
    finally:
        status_server.close()
        # Workers hand their claimed players back before Redis closes
        await pool.stop()
        # Game modes collected since the last flush would otherwise be lost
        # until the same modes are seen again.
        remaining = mode_store.drain()
        if remaining:
            try:
                if await store_game_modes(remaining, mongo_conn):
                    await refresh_game_modes_cache(mongo_conn, redis_conn)
            except Exception:
                logger.exception("Final game mode flush failed")
        await cr_api.close()
        await redis_conn.close()
        mongo_conn.close()
        # Last, because it also closes key_redis, which the schedules share
        await cr_api.key_store.close()
        logger.info("Data scraper stopped")


if __name__ == "__main__":
    asyncio.run(main())

# TODO add unit testing with example data
