from datetime import datetime, timezone
from pymongo import ReturnDocument
from .connection import MongoConn
from .validation_utils import ensure_connected


async def insert_tracked_player(
    conn: MongoConn, player_tag: str, player_name: str = "Player"
) -> str:
    """
    Insert (or reactivate) a player in the `players` collection.

    - If the player doesn't exist: create with active=True.
    - If the player exists: set active=True again (reactivate).

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The unique tag of the player (e.g., "#YYRJQY28").
        player_name (str): The name to set for the player (default: "Player").

    Returns:
        str: "created", "reactivated", or "already_tracked"
    """
    try:
        await ensure_connected(conn)
        now = datetime.now().strftime("%Y-%m-%d %H-%M-%S")

        # Try to reactivate if it exists but is inactive
        res = await conn.db.players.update_one(
            {"playerTag": player_tag, "active": False},
            # Pipeline update, so the gap can be built from the stored
            # deactivatedAt in the same atomic write.
            [
                {
                    "$set": {
                        "active": True,
                        "reactivatedAt": now,
                        "updatedAt": now,
                        "consecutiveFailures": 0,
                        "consecutiveNotFound": 0,
                        # Battles played while untracked are only recovered
                        # if they still fit into the battle log, so every
                        # untracked period is kept for the player page.
                        "trackingGaps": {
                            "$concatArrays": [
                                {"$ifNull": ["$trackingGaps", []]},
                                [{"from": "$deactivatedAt", "to": now}],
                            ]
                        },
                    }
                },
                # A player deactivated after repeated 404s exists again, so
                # its old not-found streak must not count towards a new one.
                # The adaptive intervals describe the old session; a stretched
                # one would delay the first refreshes of the new session.
                {
                    "$unset": [
                        "deactivatedReason",
                        "firstNotFoundAt",
                        "syncIntervalS",
                        "profileSyncIntervalS",
                        "lastSyncNewBattles",
                    ]
                },
            ],
            upsert=False,
        )
        if res.matched_count == 1:
            return "reactivated"

        # If not matched above, upsert (create if missing, otherwise just ensure active)
        res = await conn.db.players.update_one(
            {"playerTag": player_tag},
            {
                "$set": {"playerTag": player_tag, "active": True, "updatedAt": now},
                "$setOnInsert": {
                    "insertedAt": now,
                    "playerName": player_name,
                    "syncVersion": 0,
                },
            },
            upsert=True,
        )

        if res.upserted_id is not None:
            return "created"

        return "already_tracked"

    except Exception as e:
        print(f"[DB] [ERROR] during insert/reactivate for player: {player_tag}", e)
        raise


async def deactivate_tracked_player(
    conn: MongoConn, player_tag, reason: str | None = None
) -> int:
    """
    Deactivates a player that is being tracked into the players collection.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")
        reason (str | None): Stored as deactivatedReason when the system, not
            a user, untracks the player (e.g. "not_found")

    Returns:
        int: The amount of affected players by the update

    Raises:
        Exception: If the update of the player fails
    """

    try:
        await ensure_connected(conn)
        current_time = datetime.now().strftime("%Y-%m-%d %H-%M-%S")

        fields = {"active": False, "deactivatedAt": current_time}
        if reason:
            fields["deactivatedReason"] = reason

        res = await conn.db.players.update_one(
            {"playerTag": player_tag, "active": True}, {"$set": fields}
        )

        # Return the amount of players updated
        return res.matched_count

    except Exception as e:
        print(f"[DB] [ERROR] during update: {e}")
        raise


async def record_battle_sync(
    conn: MongoConn,
    player_tag: str,
    newest_battle_time: datetime | None,
    player_name: str | None,
    new_battle_count: int,
    interval_s: float | None = None,
):
    """
    Stores the outcome of a successful battle sync on the player document.

    syncVersion is only incremented when the sync found new battles. Cached
    statistics of a player without new battles therefore stay valid.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The unique tag of the player (e.g., "#YYRJQY28").
        newest_battle_time (datetime | None): battleTime of the newest battle in
            the fetched battle log, None if the log was empty.
        player_name (str | None): Current player name, None to keep the stored one.
        new_battle_count (int): Battles newer than the previous watermark,
            whether or not this attempt inserted them. Stored as
            lastSyncNewBattles, so the capacity estimate can price the player
            for any base interval.
        interval_s (float | None): Seconds until the player's next battle sync.
            Stored so the next interval can grow from it, and so a rebuilt
            schedule keeps the player's rhythm.

    Raises:
        Exception: Any exception that occurs during the database update operation.
    """

    try:
        await ensure_connected(conn)

        update = {
            "$set": {
                "lastBattlesSyncAt": datetime.now(timezone.utc),
                "lastSyncNewBattles": new_battle_count,
                "consecutiveFailures": 0,
                "consecutiveNotFound": 0,
            },
            "$unset": {"firstNotFoundAt": ""},
        }
        if player_name:
            update["$set"]["playerName"] = player_name
        if interval_s is not None:
            update["$set"]["syncIntervalS"] = interval_s
        if newest_battle_time is not None:
            # $max keeps the watermark from moving backwards if two syncs of
            # the same player overlap after an expired claim.
            update["$max"] = {"lastBattleTime": newest_battle_time}
        # TODO Keep a roughly accurate stored battle count per tracked player
        # (e.g. battleCount), so pages and the explore list need no count over
        # the battles collection. Not per insert: either $inc it here by the
        # sync's new battles (one write per sync, already batched), or let a
        # periodic job recount from the referencePlayerTag index. The $inc
        # drifts, e.g. when two overlapping syncs after an expired claim both
        # count the same battles, or when battles are removed later, so it
        # could be combined with a rare recount. "Somewhat accurate" is
        # enough; store when it was last counted (battleCountAt) next to it.
        if new_battle_count > 0:
            update["$inc"] = {"syncVersion": 1}

        await conn.db.players.update_one(
            {"playerTag": player_tag}, update, upsert=False
        )

    except Exception as e:
        print(f"[DB] [ERROR] recording the battle sync of {player_tag}", e)
        raise


async def record_battle_sync_failure(
    conn: MongoConn, player_tag: str, not_found: bool = False
) -> dict:
    """
    Counts a failed battle sync on the player document.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The unique tag of the player (e.g., "#YYRJQY28").
        not_found (bool): The Clash Royale API answered 404 for the player.

    Returns:
        dict: consecutiveFailures, consecutiveNotFound and firstNotFoundAt after
            this failure (empty if the player doesn't exist)

    Raises:
        Exception: Any exception that occurs during the database update operation.
    """

    try:
        await ensure_connected(conn)
        now = datetime.now(timezone.utc)

        fields = {
            "consecutiveFailures": {
                "$add": [{"$ifNull": ["$consecutiveFailures", 0]}, 1]
            },
        }
        if not_found:
            # Keep the first 404 of a streak, so deactivation can require the
            # player to be missing over a minimum time span.
            fields["consecutiveNotFound"] = {
                "$add": [{"$ifNull": ["$consecutiveNotFound", 0]}, 1]
            }
            fields["firstNotFoundAt"] = {"$ifNull": ["$firstNotFoundAt", now]}

        doc = await conn.db.players.find_one_and_update(
            {"playerTag": player_tag},
            [{"$set": fields}],
            projection={
                "_id": 0,
                "consecutiveFailures": 1,
                "consecutiveNotFound": 1,
                "firstNotFoundAt": 1,
            },
            return_document=ReturnDocument.AFTER,
        )
        return doc or {}

    except Exception as e:
        print(f"[DB] [ERROR] recording the failed sync of {player_tag}", e)
        raise
