from datetime import datetime, timezone

from .connection import MongoConn
from .validation_utils import ensure_connected


async def save_top_season(
    conn: MongoConn, season: str, players: list[dict], top_n: int
) -> None:
    """
    Stores the complete leaderboard snapshot of a season for the rollover.

    The rollover applies it from here, so a crash halfway resumes from the
    same list instead of a second fetch that may rank players differently.
    A snapshot fetched again for a changed TOP_PLAYERS_N keeps appliedAt, so
    the API's top list stays on this season instead of falling back to the
    one before while the new list is applied; appliedTopN tells the rollover
    it is not applied yet.

    Args:
        conn (MongoConn): Active connection to the mongo database
        season (str): Season id as "YYYY-MM"
        players (list[dict]): {"tag", "name", "rank"} per listed player
        top_n (int): The TOP_PLAYERS_N the list was fetched for, so a changed
            setting is noticed and applied

    Raises:
        Exception: If the write fails
    """

    try:
        await ensure_connected(conn)
        await conn.db.top_seasons.update_one(
            {"_id": season},
            {
                "$set": {
                    "players": players,
                    "topN": top_n,
                    "fetchedAt": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )

    except Exception as e:
        print(f"[DB] [ERROR] saving the top players of {season}: {e}")
        raise


async def mark_top_season_applied(conn: MongoConn, season: str, top_n: int) -> None:
    """
    Records that a season's snapshot is fully applied to the players.

    Args:
        conn (MongoConn): Active connection to the mongo database
        season (str): Season id as "YYYY-MM"
        top_n (int): The TOP_PLAYERS_N the applied list was fetched for

    Raises:
        Exception: If the write fails
    """

    try:
        await ensure_connected(conn)
        await conn.db.top_seasons.update_one(
            {"_id": season},
            {"$set": {"appliedAt": datetime.now(timezone.utc), "appliedTopN": top_n}},
        )

    except Exception as e:
        print(f"[DB] [ERROR] marking the top players of {season} applied: {e}")
        raise
