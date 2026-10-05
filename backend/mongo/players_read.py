from .connection import MongoConn
from .validation_utils import ensure_connected


async def check_player_tracked(conn: MongoConn, player_tag: str):
    """
    Checks if a given player tag is in the 'players' collection and has the field 'active=true'

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        bool: True if player is in collection and active; False otherwise

    Raises:
        Exception: If fetching tracked players fails
    """
    try:
        await ensure_connected(conn)

        doc = await conn.db.players.find_one(
            {"playerTag": player_tag, "active": True}, {"_id": 1}
        )

        if not doc:
            return False
        return True

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the tracked players: {e}")
        raise


async def get_tracked_player_tags(conn: MongoConn):
    """
    Retrieves a set of all players tags that are tracked.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        set: Tags of the active players

    Raises:
        Exception: If fetching tracked players fails
    """

    try:
        await ensure_connected(conn)

        cursor = conn.db.players.find(
            # only active/tracked players
            {"active": True},
            # projection: only fetch player tags
            {"_id": 0, "playerTag": 1},
        ).sort(
            "playerTag", 1
        )  # sort ascending

        # Turn the player tags into a set to avoid duplicates if those were
        # to happen in the players collection
        tags = set()
        async for doc in cursor:
            tags.add(doc["playerTag"])
        return tags

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the tracked players tags: {e}")
        raise


async def get_tracked_players(conn: MongoConn):
    """
    Retrieves a set of all players tags and names that are tracked.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        dict: Tags of the active players and their names

    Raises:
        Exception: If fetching tracked players fails
    """

    try:
        await ensure_connected(conn)

        cursor = conn.db.players.find(
            # only active/tracked players
            {"active": True},
            # projection: only fetch player tags and names
            {"_id": 0, "playerTag": 1, "playerName": 1},
        ).sort(
            "playerTag", 1
        )  # sort ascending

        # Return a dict
        players = {}
        async for doc in cursor:
            players[doc["playerTag"]] = doc.get("playerName")
        return players

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the tracked players: {e}")
        raise


async def get_players_count(conn: MongoConn):
    """
    Gets the number of currently tracked (active) players.

    Untracked players keep their document for a possible reactivation, so the
    total number of documents would overstate the tracked players.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        int: Number of active players

    Raises:
        Exception: If query fails
    """

    try:
        await ensure_connected(conn)
        count = await conn.db.players.count_documents({"active": True})
        return count
    except Exception as e:
        print(f"[DB] [ERROR] fetching document count: {e}")
        raise


def _tracking_gaps(doc: dict) -> list[dict]:
    """Untracked periods of a player. A never reactivated player has no field."""

    return [
        gap for gap in doc.get("trackingGaps", []) if gap.get("from") and gap.get("to")
    ]


async def get_tracked_player_cache_state(conn: MongoConn, player_tag: str):
    """
    Fetches the cache relevant state of a tracked player in one lookup.

    Combines the tracked check with the player's cache version, so player routes
    need no additional query to build version-scoped cache keys.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        dict | None: {"syncVersion": int, "firstSyncPending": bool,
            "lastBattlesSyncAt": datetime | None, "insertedAt": str | None,
            "playerName": str | None, "trackingGaps": list[dict]} if the
            player is tracked, otherwise
            None. firstSyncPending stays True until the data scraper finished
            the player's first battle sync. trackingGaps holds the untracked
            periods as {"from", "to"} timestamp strings.

    Raises:
        Exception: If the lookup fails
    """
    try:
        await ensure_connected(conn)

        doc = await conn.db.players.find_one(
            {"playerTag": player_tag, "active": True},
            {
                "_id": 0,
                "syncVersion": 1,
                "lastBattlesSyncAt": 1,
                "insertedAt": 1,
                "playerName": 1,
                "trackingGaps": 1,
            },
        )

        if not doc:
            return None
        return {
            "syncVersion": doc["syncVersion"],
            "firstSyncPending": doc.get("lastBattlesSyncAt") is None,
            "lastBattlesSyncAt": doc.get("lastBattlesSyncAt"),
            "insertedAt": doc.get("insertedAt"),
            "playerName": doc.get("playerName"),
            "trackingGaps": _tracking_gaps(doc),
        }

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the tracked player state: {e}")
        raise


async def get_player_sync_state(conn: MongoConn, player_tag: str):
    """
    Fetches the fields a battle sync needs for a single player.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        dict | None: The player document limited to active, lastBattleTime,
            playerName, consecutiveFailures, syncIntervalS, lastProfileSyncAt and
            profileSyncIntervalS, or None if the player doesn't exist

    Raises:
        Exception: If the lookup fails
    """
    try:
        await ensure_connected(conn)

        return await conn.db.players.find_one(
            {"playerTag": player_tag},
            {
                "_id": 0,
                "active": 1,
                "lastBattleTime": 1,
                "playerName": 1,
                "consecutiveFailures": 1,
                "syncIntervalS": 1,
                "lastProfileSyncAt": 1,
                "profileSyncIntervalS": 1,
            },
        )

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the sync state of {player_tag}: {e}")
        raise


async def get_tracked_players_sync_times(conn: MongoConn):
    """
    Retrieves the sync times, intervals and last activity of every tracked player.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        dict: Player tag mapped to {"lastBattlesSyncAt", "lastProfileSyncAt",
            "syncIntervalS", "profileSyncIntervalS", "lastSyncNewBattles"}.
            Times are naive UTC datetimes. A value is None if the player was
            never synced or has no interval yet.

    Raises:
        Exception: If fetching tracked players fails
    """

    try:
        await ensure_connected(conn)

        cursor = conn.db.players.find(
            {"active": True},
            {
                "_id": 0,
                "playerTag": 1,
                "lastBattlesSyncAt": 1,
                "lastProfileSyncAt": 1,
                "syncIntervalS": 1,
                "profileSyncIntervalS": 1,
                "lastSyncNewBattles": 1,
            },
        )

        players = {}
        async for doc in cursor:
            players[doc["playerTag"]] = {
                "lastBattlesSyncAt": doc.get("lastBattlesSyncAt"),
                "lastProfileSyncAt": doc.get("lastProfileSyncAt"),
                "syncIntervalS": doc.get("syncIntervalS"),
                "profileSyncIntervalS": doc.get("profileSyncIntervalS"),
                "lastSyncNewBattles": doc.get("lastSyncNewBattles"),
            }
        return players

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the tracked players sync times: {e}")
        raise
