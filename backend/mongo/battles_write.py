from pymongo.errors import BulkWriteError
from .battles_read import split_battle_copies
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
# NOTE A battle is stored once for all participants (normalize_battle in the
# data scraper's clean.py), including untracked ones. Thousands of untracked
# tags already have dozens to hundreds of battles on record through tracked
# opponents and teammates, and a player tracked later inherits them
# (count_battles_in_ranges). Per player rollups therefore have to decide what
# happens to those participants when a raw battle is compacted:
#   - Rolled up for tracked players only: the untracked participants' share is
#     lost with the raw battle, so a player tracked after the cutoff starts
#     with none of its older battles, while its passive battles from before
#     the cutoff are still raw. Either accept and show that, or keep raw
#     battles with an untracked participant (most of them) uncompacted.
#   - Rolled up for every participant: complete, but most rollups belong to
#     tags nobody looks at, which eats into the storage that compaction saves.
#   - Tracking a player has to fold its not yet compacted raw battles into the
#     rollups (or leave them to the next batch run), without counting a
#     battle twice for a player that the earlier run already covered.
# Global statistics (meta, card and deck usage) need rollups per battle, not
# sums of per player rollups, which would count every battle between two
# tracked players twice.


async def insert_battles(conn: MongoConn, battle_logs) -> list:
    """
    Inserts normalized battles into the battles collection.

    A battle that two tracked players both have in their logs is stored
    once: the first stored copy wins. Copies are found by
    split_battle_copies, since two logs can disagree on the time by a second,
    and otherwise by the _id, which is built from the time, every participant
    and their decks (battle_id in the data scraper's clean.py).

    The lookup and the insert are separate operations. Two syncs that store
    copies with different times, and both look up before either inserts,
    keep both copies, and the battle counts twice for its players. That
    takes two tracked participants synced at nearly the same moment, with
    logs that disagree on the time, and the window grows with Mongo's
    latency under load.

    Args:
        conn (MongoConn): Active connection to the mongo database
        battle_logs (list): List of normalized battle dictionaries to insert

    Returns:
        list: The newly inserted battles. A battle another player's sync
            stored first is not included.

    Raises:
        ValueError: If battle_logs is not a list
        Exception: If insertion fails for a reason other than duplicates
    """

    try:
        await ensure_connected(conn)

        if not isinstance(battle_logs, list):
            raise ValueError("battle_logs must be a list of dictionaries.")

        new_battles, _ = await split_battle_copies(conn, battle_logs)
        if not new_battles:
            return []

        await conn.db.battles.insert_many(new_battles, ordered=False)
        return new_battles

    except BulkWriteError as bwe:
        # Duplicates (E11000) are expected when a copy was stored in between.
        # The _id includes the decks, so a duplicate is always the same
        # battle, never another one that happens to share time and players.
        # Any other write error still has to surface, and so does a write
        # concern error: the inserts were not confirmed, and the caller would
        # advance the watermark past them. all() of no write errors is True,
        # so the write concern check cannot rely on it.
        write_errors = bwe.details.get("writeErrors", [])
        if not bwe.details.get("writeConcernErrors") and all(
            err.get("code") == 11000 for err in write_errors
        ):
            skipped = {err["index"] for err in write_errors}
            return [b for i, b in enumerate(new_battles) if i not in skipped]
        print(f"[DB] Bulk write error: {bwe.details}")
        raise
    except Exception as e:
        print(f"[DB] [ERROR] during insertion: {e}")
        raise
