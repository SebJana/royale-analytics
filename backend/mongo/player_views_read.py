from datetime import datetime

from .connection import MongoConn
from .validation_utils import ensure_connected


async def sum_player_views(conn: MongoConn, since: datetime) -> dict[str, int]:
    """Sum the daily visitors of every viewed player since a day.

    Visitors, not visits: one address adds at most one per player and day, so
    the sum is what ranks the explore list.

    Args:
        conn (MongoConn): Active connection to the mongo database
        since (datetime): First day (UTC midnight) included in the sum

    Returns:
        dict: Player tag mapped to its visitors; players without any are left out

    Raises:
        Exception: If the aggregation fails
    """

    try:
        await ensure_connected(conn)
        cursor = conn.db.player_views.aggregate(
            [
                {"$match": {"day": {"$gte": since}}},
                {"$group": {"_id": "$playerTag", "visitors": {"$sum": "$visitors"}}},
            ]
        )
        return {doc["_id"]: doc["visitors"] async for doc in cursor if doc["visitors"]}

    except Exception as e:
        print(f"[DB] [ERROR] summing the player views since {since}: {e}")
        raise


async def get_player_view_baselines(
    conn: MongoConn, since: datetime, before: datetime
) -> dict[str, tuple[int, int]]:
    """Visitors and days with views of every player in a range of days.

    Args:
        conn (MongoConn): Active connection to the mongo database
        since (datetime): First day (UTC midnight) included
        before (datetime): First day (UTC midnight) no longer included

    Returns:
        dict: Player tag mapped to (visitors summed, days with a bucket)

    Raises:
        Exception: If the aggregation fails
    """

    try:
        await ensure_connected(conn)
        cursor = conn.db.player_views.aggregate(
            [
                {"$match": {"day": {"$gte": since, "$lt": before}}},
                {
                    "$group": {
                        "_id": "$playerTag",
                        "visitors": {"$sum": "$visitors"},
                        "days": {"$sum": 1},
                    }
                },
            ]
        )
        return {doc["_id"]: (doc["visitors"], doc["days"]) async for doc in cursor}

    except Exception as e:
        print(f"[DB] [ERROR] reading the player view baselines since {since}: {e}")
        raise


async def get_player_views_on(conn: MongoConn, day: datetime) -> dict[str, int]:
    """Visitors of every player viewed on one day.

    Args:
        conn (MongoConn): Active connection to the mongo database
        day (datetime): The day (UTC midnight)

    Returns:
        dict: Player tag mapped to its visitors that day

    Raises:
        Exception: If the lookup fails
    """

    try:
        await ensure_connected(conn)
        cursor = conn.db.player_views.find(
            {"day": day}, {"_id": 0, "playerTag": 1, "visitors": 1}
        )
        return {doc["playerTag"]: doc.get("visitors", 0) async for doc in cursor}

    except Exception as e:
        print(f"[DB] [ERROR] fetching the player views of {day}: {e}")
        raise
