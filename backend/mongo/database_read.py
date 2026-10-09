import time
from datetime import datetime

from pymongo.errors import OperationFailure

from .connection import MongoConn
from .validation_utils import ensure_connected


async def get_server_time(conn: MongoConn) -> datetime:
    """
    Returns the Mongo server's current time.

    For comparisons with times the server wrote itself ($$NOW,
    $currentDate): this process's clock may run ahead or behind.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        datetime: Server time as naive UTC, like every datetime read back

    Raises:
        Exception: If Mongo is unreachable
    """

    try:
        await ensure_connected(conn)
        return (await conn.db.command("hello"))["localTime"]
    except Exception as e:
        print(f"[DB] [ERROR] reading the server time: {e}")
        raise


async def get_database_health(conn: MongoConn) -> dict:
    """
    Collects the storage size and health signals of the application database.

    Every value comes from metadata (dbStats, serverStatus, the collection
    count), so the call stays cheap enough for frequent monitoring.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        dict: pingMs (round trip of one ping), dataSize (uncompressed data),
            storageSize and indexSize (on disk, bytes), totalSize (their sum),
            fsUsedSize and fsTotalSize (the volume Mongo stores its files on),
            connections (open client connections, None without the
            serverStatus privilege) and battlesTotal (estimated)

    Raises:
        Exception: If Mongo is unreachable or a required command fails
    """

    try:
        await ensure_connected(conn)
        started = time.perf_counter()
        await conn.db.command("ping")
        ping_ms = (time.perf_counter() - started) * 1000

        stats = await conn.db.command("dbStats")
        try:
            server = await conn.client.admin.command("serverStatus")
            connections = server["connections"]["current"]
        except OperationFailure:
            # serverStatus needs the clusterMonitor role, which an application
            # user does not always have. The other values do not depend on it.
            connections = None

        return {
            "pingMs": round(ping_ms, 1),
            "dataSize": int(stats.get("dataSize", 0)),
            "storageSize": int(stats.get("storageSize", 0)),
            "indexSize": int(stats.get("indexSize", 0)),
            "totalSize": int(
                stats.get("totalSize")
                or stats.get("storageSize", 0) + stats.get("indexSize", 0)
            ),
            "fsUsedSize": int(stats["fsUsedSize"]) if "fsUsedSize" in stats else None,
            "fsTotalSize": (
                int(stats["fsTotalSize"]) if "fsTotalSize" in stats else None
            ),
            "connections": connections,
            "battlesTotal": await conn.db.battles.estimated_document_count(),
        }
    except Exception as e:
        print(f"[DB] [ERROR] collecting the database health: {e}")
        raise
