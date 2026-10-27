from .connection import MongoConn
from .validation_utils import ensure_connected


async def get_top_season(conn: MongoConn, season: str) -> dict | None:
    """
    Fetches the stored leaderboard snapshot of a season.

    Args:
        conn (MongoConn): Active connection to the mongo database
        season (str): Season id as "YYYY-MM"

    Returns:
        dict | None: {"_id", "players": [{"tag", "name", "rank"}], "topN",
            "fetchedAt", "appliedAt", "appliedTopN"} (the last two once a
            rollover finished; appliedTopN differs from topN while a list
            fetched again for a changed TOP_PLAYERS_N is applied), or None if
            the season was never fetched

    Raises:
        Exception: If the lookup fails
    """

    try:
        await ensure_connected(conn)
        return await conn.db.top_seasons.find_one({"_id": season})

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the top players of {season}: {e}")
        raise


async def get_top_season_page(
    conn: MongoConn, season: str | None, after_rank: int, limit: int
) -> dict | None:
    """
    Retrieves one page of an applied season's top list, best first.

    Read from the stored snapshot, not the player documents: the snapshot of
    a season never changes once applied, so pages read before and after a
    rollover still belong to the same list, and its ranks increase strictly,
    so the rank cursor skips nobody.

    Args:
        conn (MongoConn): Active connection to the mongo database
        season (str | None): Season id as "YYYY-MM", None for the newest
            applied season
        after_rank (int): Last rank of the previous page, 0 for the first
        limit (int): Maximum number of players in the page

    Returns:
        dict | None: {"_id": season, "players": [{"tag", "name", "rank"}]},
            or None if no such season is applied

    Raises:
        Exception: If the lookup fails
    """

    try:
        await ensure_connected(conn)
        match = {"appliedAt": {"$exists": True}}
        if season is not None:
            match["_id"] = season
        pipeline = [
            {"$match": match},
            # Zero-padded ids sort like the seasons themselves
            {"$sort": {"_id": -1}},
            {"$limit": 1},
            {
                "$project": {
                    "players": {
                        "$slice": [
                            {
                                "$filter": {
                                    "input": "$players",
                                    "cond": {"$gt": ["$$this.rank", after_rank]},
                                }
                            },
                            limit,
                        ]
                    }
                }
            },
        ]
        docs = await conn.db.top_seasons.aggregate(pipeline).to_list(1)
        return docs[0] if docs else None

    except Exception as e:
        print(f"[DB] [ERROR] trying to fetch the top list of {season}: {e}")
        raise
