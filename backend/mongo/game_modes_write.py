from datetime import datetime, timezone
from .connection import MongoConn
from .validation_utils import ensure_connected


async def insert_game_modes(conn: MongoConn, game_modes: list):
    """
    Ensure each game mode has a document in 'game_modes' and set lastSeen=now.
    - If it exists: lastSeen is updated.
    - If it doesn't exist: inserted with {name, lastSeen}.

    Args:
        conn (MongoConn): Active connection to the mongo database
        game_modes: list of unique strings (mode names)

    Returns:
        dict with counts of inserted and modified game modes
    """

    try:
        await ensure_connected(conn)
        now = datetime.now(timezone.utc)

        inserted = 0
        modified = 0

        for name in game_modes:
            res = await conn.db.game_modes.update_one(
                {"name": name},
                {
                    "$set": {"lastSeen": now},
                    "$setOnInsert": {"name": name},
                },
                upsert=True,
            )

            if res.upserted_id is not None:
                inserted += 1
            elif res.modified_count > 0:
                modified += 1

        return {
            "inserted": inserted,  # how many new docs created
            "modified": modified,  # how many existing docs got lastSeen updated
        }

    except Exception as e:
        print("[DB] [ERROR] inserting game modes", e)
        raise
