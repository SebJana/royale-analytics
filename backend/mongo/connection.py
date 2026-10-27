import os
import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar

import pymongo
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ExecutionTimeout, PyMongoError


def build_uri_from_parts():
    user = os.getenv("MONGO_APP_USER")
    pwd = os.getenv("MONGO_APP_PWD")
    host = "mongo"
    port = "27017"
    db = os.getenv("MONGO_APP_DB")

    if not all([user, pwd, db]):
        raise ValueError(
            "MONGO_APP_USER, MONGO_APP_PWD, MONGO_APP_DB have to exist in .env file"
        )
    return f"mongodb://{user}:{pwd}@{host}:{port}/{db}?authSource={db}"


# A successful ping is trusted for this long. Every Mongo helper checks the
# connection first, and a ping per call adds a round trip to each operation.
# An operation that fails in between still raises from the driver.
ALIVE_CHECK_INTERVAL_S = 10.0

# Monotonic deadline of the current request's Mongo work, None outside one.
# pymongo.timeout() only bounds the driver and keeps its deadline private, so
# waits in Python, such as the reconnect lock, read it from here.
_request_deadline: ContextVar[float | None] = ContextVar(
    "mongo_request_deadline", default=None
)


@contextmanager
def request_deadline(timeout_s: float):
    """Give all Mongo work inside the block one shared deadline.

    The driver honors it through pymongo.timeout(), and MongoConn bounds its
    own waits with the time left. Tasks started inside the block inherit it.

    Args:
        timeout_s: Seconds from now until the deadline.
    """

    token = _request_deadline.set(time.monotonic() + timeout_s)
    try:
        with pymongo.timeout(timeout_s):
            yield
    finally:
        _request_deadline.reset(token)


def _time_left() -> float | None:
    """Seconds until the current deadline, None without one."""

    deadline = _request_deadline.get()
    return None if deadline is None else max(deadline - time.monotonic(), 0.0)


def is_mongo_timeout(exc: BaseException) -> bool:
    """Whether a Mongo timeout caused exc, directly or further down its chain.

    Callers that wrap every failure in their own error (``raise
    HTTPException(500) from e``, or raising inside an ``except``) keep the
    driver's exception as the cause or context, so the chain still tells a
    busy or unreachable Mongo apart from a bug.

    Args:
        exc: The exception to inspect.

    Returns:
        bool: True if any exception in the chain is a driver timeout: no
        pooled connection in time, no server selected in time, or an
        operation past its deadline.
    """

    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, PyMongoError) and exc.timeout:
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


class MongoConn:
    """
    Async MongoDB connection manager using Motor for the Clash Royale analytics application.
    Fully async implementation for all backend services.
    """

    def __init__(
        self,
        app_name: str = "default",
        max_pool_size: int = 100,
        wait_queue_timeout_s: float | None = None,
    ):
        """
        Args:
            app_name: Shown in Mongo's logs and currentOp for this client.
            max_pool_size: Most connections the client opens at once.
            wait_queue_timeout_s: Longest wait for a free pooled connection
                once all are busy, None to wait indefinitely. Past it the
                operation raises a timeout instead of queueing on.
        """
        self._uri = build_uri_from_parts()
        self._db_name = os.getenv("MONGO_APP_DB")
        self._app_name = app_name
        self._max_pool_size = max_pool_size
        self._wait_queue_timeout_ms = (
            None if wait_queue_timeout_s is None else int(wait_queue_timeout_s * 1000)
        )
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self.is_connected = False
        self._last_alive_at = 0.0
        # Concurrent callers that all see a failed ping reconnect once, instead
        # of each creating a client that is never closed.
        self._reconnect_lock = asyncio.Lock()

    async def connect(self):
        """Connect to the database and send a test ping.

        The previous client is closed only after the new one answered, so a
        failed reconnect leaves the old client in place for the next attempt.
        """
        # No client-wide timeoutMS: it would also cut off long jobs such as
        # the search index build. Callers that need a deadline set one with
        # pymongo.timeout(), as the API does per request.
        client = AsyncIOMotorClient(
            self._uri,
            appname=self._app_name,
            maxPoolSize=self._max_pool_size,
            waitQueueTimeoutMS=self._wait_queue_timeout_ms,
        )
        try:
            await client.admin.command("ping")
        # BaseException: a caller's deadline cancels this await, and the new
        # client has to be closed then too.
        except BaseException as e:
            client.close()
            self.is_connected = False
            print(f"[DB] Failed to connect to MongoDB: {e}")
            raise

        previous = self.client
        self.client = client
        self.db = client[self._db_name]
        self.is_connected = True
        self._last_alive_at = time.monotonic()
        if previous is not None:
            previous.close()
        print("[DB] Connected to MongoDB successfully.")

    async def is_connection_alive(self):
        """Ping the database to check if it is up and running.

        A ping within the last ALIVE_CHECK_INTERVAL_S counts without a new one.
        """
        if not self.client or not self.is_connected:
            return False
        if time.monotonic() - self._last_alive_at < ALIVE_CHECK_INTERVAL_S:
            return True
        try:
            await self.client.admin.command("ping")
            self._last_alive_at = time.monotonic()
            return True
        except Exception:
            self.is_connected = False
            return False

    async def ensure_connection(self):
        """Ensure connection is alive, reconnect if necessary"""
        if await self.is_connection_alive():
            return
        # Waiting for another caller's reconnect is outside the driver, so
        # pymongo.timeout() alone would let it run past the request's deadline.
        timeout = asyncio.timeout(_time_left())
        try:
            async with timeout:
                async with self._reconnect_lock:
                    # Another caller may have reconnected while this one waited
                    if await self.is_connection_alive():
                        return
                    print("[DB] Connection lost, attempting to reconnect...")
                    await self.connect()
        except TimeoutError as e:
            if not timeout.expired():
                raise
            # A driver timeout, so callers and the API's handlers treat it
            # like any other Mongo timeout.
            raise ExecutionTimeout("Request deadline passed while reconnecting") from e

    def close(self):
        """Close the database connection"""
        if self.client:
            self.client.close()
            self.is_connected = False
            print("[DB] MongoDB connection closed.")
