from datetime import datetime

from .connection import MongoConn
from .validation_utils import ensure_connected

# deactivatedReason of a player taken off the schedules to make room. Unlike a
# removed player, it keeps its page and its place in the search.
PARKED_REASON = "parked"

# Players the search lists: tracked ones and parked ones, whose page can
# still be opened to track them again.
# Parking order of the players outside the spare window (get_park_candidates)
# NOTE Matches the keys of park_order_index in ensure_player_views_indexes
# and the init script; the index only serves the sort in this exact order.
PARK_ORDER = [("views30d", 1), ("lastViewedAt", 1), ("insertedAt", 1), ("playerTag", 1)]

LISTED_PLAYER_FILTER = {"$or": [{"active": True}, {"deactivatedReason": PARKED_REASON}]}


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
    Retrieves the tags and names of every player the search lists.

    Parked players are included: their page still works.

    Args:
        conn (MongoConn): Active connection to the mongo database

    Returns:
        dict: Tags of the active and parked players and their names

    Raises:
        Exception: If fetching tracked players fails
    """

    try:
        await ensure_connected(conn)

        cursor = conn.db.players.find(
            LISTED_PLAYER_FILTER,
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


async def get_tracked_players_page(
    conn: MongoConn, after_tag: str | None, limit: int
) -> list[tuple[str, str | None]]:
    """
    Retrieves one page of the players the search lists, ordered by tag.

    Pages follow the unique tag index, so each one is a short indexed read.
    Reading all players page by page keeps every read and its decoding short,
    where one cursor over all players decodes batches of up to 16 MB at once.

    Args:
        conn (MongoConn): Active connection to the mongo database
        after_tag (str | None): Last tag of the previous page, None for the first
        limit (int): Maximum number of players in the page

    Returns:
        list: (tag, name) pairs; fewer than limit only on the last page

    Raises:
        Exception: If fetching the players fails
    """

    try:
        await ensure_connected(conn)
        query = dict(LISTED_PLAYER_FILTER)
        if after_tag is not None:
            query["playerTag"] = {"$gt": after_tag}
        cursor = (
            conn.db.players.find(query, {"_id": 0, "playerTag": 1, "playerName": 1})
            .sort("playerTag", 1)
            .limit(limit)
        )
        return [(doc["playerTag"], doc.get("playerName")) async for doc in cursor]

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch tracked players after {after_tag}: {e}")
        raise


async def get_players_changed_since(
    conn: MongoConn, since: datetime
) -> dict[str, tuple[str | None, bool]]:
    """
    Retrieves every player whose name or tracking state changed since a time.

    Inactive players are included, so a reader also learns about
    deactivations. Players without searchChangedAt (last changed before the
    field existed) never match; a full read covers them.

    Args:
        conn (MongoConn): Active connection to the mongo database
        since (datetime): Mongo server time (naive UTC); players whose
            searchChangedAt is at or after it are returned

    Returns:
        dict: Player tag mapped to (name, listed), listed being whether the
            search lists the player (active or parked)

    Raises:
        Exception: If fetching the players fails
    """

    try:
        await ensure_connected(conn)
        cursor = conn.db.players.find(
            {"searchChangedAt": {"$gte": since}},
            {
                "_id": 0,
                "playerTag": 1,
                "playerName": 1,
                "active": 1,
                "deactivatedReason": 1,
            },
        )
        return {
            doc["playerTag"]: (
                doc.get("playerName"),
                bool(doc.get("active"))
                or doc.get("deactivatedReason") == PARKED_REASON,
            )
            async for doc in cursor
        }

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the players changed since {since}: {e}")
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
            "lastBattlesSyncAt": datetime | None,
            "lastBattleTime": datetime | None, "insertedAt": str | None,
            "ownBattlesFrom": datetime | None | "unknown",
            "playerName": str | None, "trackingGaps": list[dict],
            "exploreRank": int | None, "views30d": int, "parked": bool} if
            the player is tracked or parked, otherwise None. firstSyncPending stays True until the data scraper finished
            the player's first battle sync. lastBattleTime is the newest
            battle its own syncs found (the scraper's watermark), None before
            the first one. trackingGaps holds the untracked
            periods as {"from", "to"} timestamp strings.

    Raises:
        Exception: If the lookup fails
    """
    try:
        await ensure_connected(conn)

        doc = await conn.db.players.find_one(
            {"playerTag": player_tag, **LISTED_PLAYER_FILTER},
            {
                "_id": 0,
                "active": 1,
                "syncVersion": 1,
                "lastBattlesSyncAt": 1,
                "lastBattleTime": 1,
                "insertedAt": 1,
                "ownBattlesFrom": 1,
                "playerName": 1,
                "trackingGaps": 1,
                "exploreRank": 1,
                "views30d": 1,
            },
        )

        if not doc:
            return None
        return {
            "syncVersion": doc["syncVersion"],
            "firstSyncPending": doc.get("lastBattlesSyncAt") is None,
            "lastBattlesSyncAt": doc.get("lastBattlesSyncAt"),
            "lastBattleTime": doc.get("lastBattleTime"),
            "insertedAt": doc.get("insertedAt"),
            # Absent for players tracked before it was recorded
            "ownBattlesFrom": doc.get("ownBattlesFrom", "unknown"),
            "playerName": doc.get("playerName"),
            "trackingGaps": _tracking_gaps(doc),
            "exploreRank": doc.get("exploreRank"),
            "views30d": doc.get("views30d", 0),
            "parked": not doc.get("active"),
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


async def get_explore_page(conn: MongoConn, after_rank: int, limit: int) -> list[dict]:
    """
    Retrieves one page of the explore list, most viewed players first.

    Pages follow exploreRank, which the ranking job writes, so the cursor is
    a rank and no exact view count is needed to continue a page.

    Args:
        conn (MongoConn): Active connection to the mongo database
        after_rank (int): Last rank of the previous page, 0 for the first
        limit (int): Maximum number of players in the page

    Returns:
        list: {"playerTag", "playerName", "exploreRank", "views30d"} dicts

    Raises:
        Exception: If fetching the players fails
    """

    try:
        await ensure_connected(conn)
        cursor = (
            conn.db.players.find(
                # A player untracked since the last ranking keeps its rank
                # until the next one, so active is checked here as well.
                {"exploreRank": {"$gt": after_rank}, "active": True},
                {
                    "_id": 0,
                    "playerTag": 1,
                    "playerName": 1,
                    "exploreRank": 1,
                    "views30d": 1,
                },
            )
            .sort("exploreRank", 1)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the explore page after {after_rank}: {e}")
        raise


async def get_park_candidates(
    conn: MongoConn, limit: int, spared_since: datetime
) -> list[str]:
    """
    Retrieves the active players to park first, least viewed first.

    Order: views30d ascending, then lastViewedAt ascending (never viewed
    first, as a missing field sorts before every date), then insertedAt, then
    the tag, so equal players always come in the same order. Players added,
    reactivated or viewed since spared_since come only after all others, the
    least recently active of them first, so the limit can always be reached
    while any active player is left.

    The first read follows park_order_index (ensure_player_views_indexes)
    and stops at the limit. The second only runs when every other player is
    taken, and reads the few recent ones.

    Args:
        conn (MongoConn): Active connection to the mongo database
        limit (int): Maximum number of players to return
        spared_since (datetime): Start of the spare window (aware UTC)

    Returns:
        list: Player tags in parking order

    Raises:
        Exception: If fetching the players fails
    """

    try:
        await ensure_connected(conn)
        # activatedAt is missing on players tracked before it was recorded.
        # A missing field never matches $gte, so they count as outside the
        # window, like players nobody opened.
        recent = {
            "$or": [
                {"activatedAt": {"$gte": spared_since}},
                {"lastViewedAt": {"$gte": spared_since}},
            ]
        }
        tags = [
            doc["playerTag"]
            async for doc in conn.db.players.find(
                {"active": True, "$nor": [recent]}, {"_id": 0, "playerTag": 1}
            )
            .sort(PARK_ORDER)
            .limit(limit)
        ]
        if len(tags) < limit:
            # Only recent players are left. The one active longest ago goes
            # first, so a player just tracked again is not the next to go.
            pipeline = [
                {"$match": {"active": True, **recent}},
                {
                    "$addFields": {
                        "lastActivity": {"$max": ["$activatedAt", "$lastViewedAt"]}
                    }
                },
                {"$sort": {"lastActivity": 1, "playerTag": 1}},
                {"$limit": limit - len(tags)},
                {"$project": {"_id": 0, "playerTag": 1}},
            ]
            tags += [
                doc["playerTag"] async for doc in conn.db.players.aggregate(pipeline)
            ]
        return tags

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the players to park: {e}")
        raise
