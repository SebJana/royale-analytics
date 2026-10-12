from datetime import datetime, timezone

from .connection import MongoConn
from .validation_utils import ensure_connected

# How long a daily bucket of views is kept after its day.
# NOTE Has to cover VIEWS_WINDOW_DAYS in the data scraper settings, and
# matches the TTL in db/mongo-init/01-init.js. Mongo keeps the TTL of an
# existing index, so changing it needs a collMod on player_views.day_ttl.
PLAYER_VIEWS_RETENTION_S = 35 * 24 * 60 * 60  # 35 days


async def ensure_player_views_indexes(conn: MongoConn) -> None:
    """Create the indexes of the view counts and the explore ranking, if missing.

    The API creates them at startup, before it counts the first view: without
    the unique index, two first views of a player on a day can create two
    buckets, and the index can then never be built. The data scraper creates
    them too, as it owns the ranking. The init script only runs on an empty
    volume, so existing databases need them here.

    Args:
        conn (MongoConn): Active MongoDB connection instance.

    Raises:
        Exception: If an index cannot be created.
    """

    try:
        await ensure_connected(conn)
        await conn.db.player_views.create_index(
            [("playerTag", 1), ("day", 1)], unique=True, name="playerTag_day_unique"
        )
        # NOTE Mongo refuses to change expireAfterSeconds through
        # create_index; a new retention needs collMod or a dropped index.
        await conn.db.player_views.create_index(
            "day", expireAfterSeconds=PLAYER_VIEWS_RETENTION_S, name="day_ttl"
        )
        # Explore pages and the popular badge read players by rank. Only
        # ranked players have the field, so the index stays small.
        await conn.db.players.create_index(
            "exploreRank",
            name="exploreRank_index",
            partialFilterExpression={"exploreRank": {"$exists": True}},
        )
    except Exception as e:
        print(f"[DB] [ERROR] creating the player views indexes: {e}")
        raise


async def record_profile_view(
    conn: MongoConn, player_tag: str, day: str, new_visitor: bool
) -> None:
    """Count one visit of a player's profile.

    Only counts are stored, never who visited (see the API's
    helpers/profile_views.py). visits counts every counted visit, visitors
    only a visitor's first one of the day; the explore ranking sums visitors.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The viewed player's tag (e.g., "#YYRJQY28").
        day (str): UTC day of the visit (YYYY-MM-DD).
        new_visitor (bool): The visitor's first counted visit to the player
            that day.

    Raises:
        Exception: If a write fails.
    """

    try:
        await ensure_connected(conn)
        bucket = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        await conn.db.player_views.update_one(
            {"playerTag": player_tag, "day": bucket},
            {"$inc": {"visits": 1, "visitors": int(new_visitor)}},
            upsert=True,
        )
        await conn.db.players.update_one(
            {"playerTag": player_tag},
            {
                "$inc": {"viewsTotal": 1},
                "$set": {"lastViewedAt": datetime.now(timezone.utc)},
            },
        )
    except Exception as e:
        print(f"[DB] [ERROR] recording a profile view of {player_tag}: {e}")
        raise
