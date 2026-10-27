from .connection import MongoConn
from .validation_utils import ensure_connected


async def get_player_profile(conn: MongoConn, player_tag: str):
    """
    Fetches the latest stored profile snapshot of a player.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        dict | None: {"profile": <Clash Royale player response>, "syncedAt": datetime},
            or None if no snapshot was stored yet

    Raises:
        Exception: If the lookup fails
    """

    try:
        await ensure_connected(conn)
        return await conn.db.player_profiles.find_one(
            {"_id": player_tag}, {"_id": 0, "profile": 1, "syncedAt": 1}
        )
    except Exception as e:
        print(f"[DB] [ERROR] fetching the profile of {player_tag}: {e}")
        raise
