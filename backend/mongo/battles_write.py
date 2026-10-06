from pymongo.errors import BulkWriteError
from .connection import MongoConn
from .validation_utils import ensure_connected

# TODO Evaluate compacting old battles into aggregated stats to save storage.
# Raw battles are kept indefinitely for now, so the data can accumulate first.
# Before building anything, measure: storage per battle and growth per month,
# and how much smaller rollups would be (e.g. per player, day, deck and game
# mode: games, wins, losses, crowns, cards). Then decide on a cutoff (after X
# months or seasons) past which raw battles are replaced by those rollups.
# Constraint: the deck, card and daily stats (battles_read.py) aggregate raw
# battles over arbitrary date ranges, and the battle list shows single games,
# so compacted periods must still answer those queries or be marked as
# summary only. Raw battles cannot be recovered from the API after the battle
# log rotates, so compaction is irreversible.


async def insert_battles(conn: MongoConn, battle_logs) -> int:
    """
    Inserts battle logs into the battles collection.

    Battles that already exist are skipped by the unique
    (referencePlayerTag, battleTime) index; every other battle is still inserted.

    Args:
        conn (MongoConn): Active connection to the mongo database
        battle_logs (list): List of battle log dictionaries to insert

    Returns:
        int: Number of newly inserted battles

    Raises:
        ValueError: If battle_logs is not a list
        Exception: If insertion fails for a reason other than duplicates
    """

    try:
        await ensure_connected(conn)

        if not isinstance(battle_logs, list):
            raise ValueError("battle_logs must be a list of dictionaries.")

        if not battle_logs:
            return 0

        res = await conn.db.battles.insert_many(battle_logs, ordered=False)
        return len(res.inserted_ids)

    except BulkWriteError as bwe:
        # Duplicates (E11000) are expected when a battle was stored before.
        # Any other write error still has to surface, and so does a write
        # concern error: the inserts were not confirmed, and the caller would
        # advance the watermark past them. all() of no write errors is True,
        # so the write concern check cannot rely on it.
        write_errors = bwe.details.get("writeErrors", [])
        if not bwe.details.get("writeConcernErrors") and all(
            err.get("code") == 11000 for err in write_errors
        ):
            return bwe.details.get("nInserted", 0)
        print(f"[DB] Bulk write error: {bwe.details}")
        raise
    except Exception as e:
        print(f"[DB] [ERROR] during insertion: {e}")
        raise
