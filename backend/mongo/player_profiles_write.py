from datetime import datetime, timezone
from .connection import MongoConn
from .players_write import name_update_fields
from .validation_utils import ensure_connected


async def save_player_profile(
    conn: MongoConn, player_tag: str, profile: dict, interval_s: float | None = None
):
    """
    Stores a player's profile snapshot, replacing the previous one.

    Only the latest snapshot is kept. The profile lives in its own collection
    because a raw profile (full card collection, badges, achievements) is tens
    of KB, while the players documents are read on every request and listing.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")
        profile (dict): Player response of the Clash Royale API
        interval_s (float | None): Seconds until the next profile refresh.
            Stored so the next interval can grow from it, and so a rebuilt
            schedule keeps the player's rhythm.

    Raises:
        Exception: If one of the updates fails
    """

    try:
        await ensure_connected(conn)
        now = datetime.now(timezone.utc)

        await conn.db.player_profiles.update_one(
            {"_id": player_tag},
            {"$set": {"profile": profile, "syncedAt": now}},
            upsert=True,
        )

        update = {"lastProfileSyncAt": now}
        if interval_s is not None:
            update["profileSyncIntervalS"] = interval_s
        # The profile carries the current name as well; keep both in sync
        if profile.get("name"):
            update.update(name_update_fields(profile["name"]))
        # A pipeline update, which name_update_fields needs.
        await conn.db.players.update_one({"playerTag": player_tag}, [{"$set": update}])

    except Exception as e:
        print(f"[DB] [ERROR] saving the profile of {player_tag}: {e}")
        raise
