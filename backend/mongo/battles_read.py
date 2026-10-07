from .connection import MongoConn
from .validation_utils import ensure_connected, check_valid_time_range
from .query_utils import (
    match_tag_before_datetime_stage,
    match_tag_time_mode_range_stage,
    extract_deck_stage,
    deck_card_filter_stages,
)
from datetime import datetime
from typing import Optional, Iterable


async def get_battles_count(conn: MongoConn) -> int:
    """
    Estimates the total battle count from collection metadata to avoid a scan.

    Args:
        conn (MongoConn): Active connection to the MongoDB database.

    Returns:
        int: Estimated number of documents in the battles collection.

    Raises:
        Exception: If the connection check or count query fails.
    """

    try:
        await ensure_connected(conn)
        count = await conn.db.battles.estimated_document_count()
        return count
    except Exception as e:
        print(f"[DB] [ERROR] fetching document count: {e}")
        raise


async def print_first_battles(conn: MongoConn, limit: int = 5):
    """
    Prints collection count and previews a few documents.

    Args:
        conn (MongoConn): Active connection to the mongo database
        limit (int): Number of documents to preview (default: 5)
    """

    try:
        await ensure_connected(conn)
        # Preview first few documents
        async for doc in conn.db.battles.find().limit(limit):
            print(doc)

    except Exception as e:
        print(f"[DB] [ERROR] fetching collection info: {e}")


async def get_last_battles(
    conn: MongoConn, player_tag: str, before_datetime: datetime, limit: int = 20
):
    """
    Fetches all battles within the limited amount that the player had before a given date and time

    Args:
        conn (MongoConn): Active connection to the MongoDB database.
        player_tag (str): The tag of the player whose last battles are to be fetched.
        before_datetime (datetime.datetime): Date and time before which the game happened.
        limit (int): Amount of battles to be fetched that happened before the given datetime (default: 20)

    Returns:
        dict: Containing
            - battles (list) : a list of dictionaries containing the players last battles
            - latestBattleTime (datetime): latest battleTime of the returned battles
            - earliestBattleTime (datetime): earliest battleTime of the returned battles

    Raises:
        Exception: If there is an error while fetching the battles from the database.
    """

    try:
        await ensure_connected(conn)

        if not isinstance(before_datetime, datetime):
            raise TypeError("end_datetime must be a datetime")

        pipeline = [
            match_tag_before_datetime_stage(player_tag, before_datetime),
            # Last N matches before specified battleTime
            {"$sort": {"battleTime": -1}},
            {"$limit": limit},
            {
                "$facet": {
                    "battles": [
                        {
                            "$project": {
                                "_id": 0,
                                "battleTime": 1,
                                "gameResult": 1,
                                "gameMode": 1,
                                "team": 1,
                                "opponent": 1,
                                "arena": 1,
                            }
                        }
                    ],
                    "meta": [
                        {
                            "$group": {
                                "_id": None,
                                "latest": {"$max": "$battleTime"},
                                "earliest": {"$min": "$battleTime"},
                            }
                        }
                    ],
                }
            },
            {
                "$project": {
                    "battles": 1,
                    "latestBattleTime": {
                        "$ifNull": [{"$arrayElemAt": ["$meta.latest", 0]}, None]
                    },
                    "earliestBattleTime": {
                        "$ifNull": [{"$arrayElemAt": ["$meta.earliest", 0]}, None]
                    },
                }
            },
        ]

        res = await conn.db.battles.aggregate(pipeline, allowDiskUse=True).to_list(
            length=1
        )

        if not res:
            return {"battles": [], "latestBattleTime": None, "earliestBattleTime": None}
        return res[0]

    except Exception as e:
        print(f"[DB] [ERROR] fetching decks info: {e}")
        raise


async def get_decks_win_percentage(
    conn: MongoConn,
    player_tag: str,
    start: datetime,
    end: datetime,
    game_modes: Optional[Iterable[str]] = None,
    card_filter: Optional[dict] = None,
    sort_by: str = "battleCount",
    sort_ascending: bool = False,
    limit: int = 250,
    min_battles: int = 1,
    exclude_game_modes: Optional[Iterable[str]] = None,
):
    """
    Fetches the player's top decks of a time frame, with totals over all of them.

    Only the first `limit` decks of the sort order are returned. The totals
    cover every deck of the filter context, so the caller can tell whether
    decks were left out.

    Args:
        conn (MongoConn): Active connection to the MongoDB database.
        player_tag (str): The tag of the player whose unique decks are to be fetched.
        start (datetime): Start of the UTC window (inclusive), timezone-aware.
        end (datetime): End of the UTC window (exclusive), timezone-aware.
        game_modes (Optional[Iterable[str]]): If provided/non-empty, filter to these game modes in which the game happened.
        card_filter (Optional[dict]): Normalized card filter (see
            deck_card_filter_stages), None for all decks.
        sort_by (str): Deck field to sort by: "battleCount", "wins",
            "winRate" or "lastSeen". Match mode ranks by matchedCardCount
            first.
        sort_ascending (bool): Lowest first instead of highest first.
        limit (int): Maximum number of decks returned.
        min_battles (int): Decks played fewer times are left out of the list
            and the totals, e.g. so a deck won once does not top the win rate.
        exclude_game_modes (Optional[Iterable[str]]): If provided/non-empty,
            leave out battles in these modes. Only used without game_modes.

    Returns:
        dict: {"decks": [...], "deckCount": int, "battleCount": int,
            "wins": int, "totalBattles": int}. deckCount, battleCount and
            wins cover every deck the filters keep, decks only the first
            `limit` of them. totalBattles counts every battle of the time
            frame and modes, before the card filter. In match mode every deck
            has a matchedCardCount.
    Raises:
        Exception: If there is an error while fetching the battles from the database.
    """

    try:
        await ensure_connected(conn)
        check_valid_time_range(start, end)

        filter_stages = deck_card_filter_stages(card_filter) if card_filter else []
        if min_battles > 1:
            filter_stages.insert(0, {"$match": {"battleCount": {"$gte": min_battles}}})
        match_mode = bool(card_filter) and card_filter["mode"] == "match"

        direction = 1 if sort_ascending else -1
        sort = {"matchedCardCount": -1} if match_mode else {}
        sort[sort_by] = direction
        # Ties keep a stable order, so the cap always cuts at the same deck
        for field in ("battleCount", "lastSeen", "_id"):
            sort.setdefault(field, -1)

        pipeline = [
            # Match the relevant files for the player and the time frame
            match_tag_time_mode_range_stage(
                player_tag, start, end, game_modes, exclude_game_modes
            ),
            # TODO The query scales with battles, not decks: every matched
            # battle document is loaded for the grouping, even when only the
            # sort or min_battles changed.
            # Currently, a result that fits under DECK_STATS_LIMIT is sorted
            # and filtered in the browser (pages/player/decks.tsx), so only
            # capped results come back here for a re-sort.
            # If re-sorts of capped results get heavy, cache the grouped decks
            # of a filter context in Redis and sort, filter and cap them in
            # the API, so a re-sort skips Mongo (about 7 MB at 10k decks).
            {
                "$group": {
                    # The deck key the data scraper stores per battle
                    # (build_deck_key in clean.py): cards and tower troop,
                    # without levels or names, so neither splits a deck.
                    # Grouping on one string skips extracting and sorting
                    # every battle's cards, about 5-8x (ish) faster.
                    "_id": "$deckKey",
                    # One battle's team, for the names on display
                    "team": {"$first": "$team"},
                    "battleCount": {"$sum": 1},
                    "wins": {
                        "$sum": {"$cond": [{"$eq": ["$gameResult", "Victory"]}, 1, 0]}
                    },
                    "firstSeen": {"$min": "$battleTime"},
                    "lastSeen": {"$max": "$battleTime"},
                    "modes": {"$addToSet": "$gameMode"},
                }
            },
            {
                "$facet": {
                    # The top decks. Only those few are extracted for display.
                    "decks": [
                        *filter_stages,
                        {
                            "$addFields": {
                                "winRate": {
                                    "$multiply": [
                                        {"$divide": ["$wins", "$battleCount"]},
                                        100,
                                    ]
                                }
                            }
                        },
                        {"$sort": sort},
                        {"$limit": limit},
                        extract_deck_stage(player_tag),
                        {
                            "$project": {
                                "_id": 0,
                                # {id, name, evolutionLevel} per card, the
                                # evolutions first, then by id
                                "deck": {
                                    "$sortArray": {
                                        "input": {
                                            "$map": {
                                                "input": "$deck.cards",
                                                "as": "card",
                                                "in": {
                                                    "id": "$$card.id",
                                                    "name": "$$card.name",
                                                    "evolutionLevel": "$$card.evolutionLevel",
                                                },
                                            }
                                        },
                                        "sortBy": {"evolutionLevel": -1, "id": 1},
                                    }
                                },
                                # {id, name}, [] without a tower troop
                                "support": {
                                    "$sortArray": {
                                        "input": "$deck.support",
                                        "sortBy": {"id": 1},
                                    }
                                },
                                "battleCount": 1,
                                "wins": 1,
                                "winRate": 1,
                                "firstSeen": 1,
                                "lastSeen": 1,
                                "modes": 1,
                                **({"matchedCardCount": 1} if match_mode else {}),
                            }
                        },
                    ],
                    # Totals over every deck the filters keep, not only the
                    # returned ones
                    "matching": [
                        *filter_stages,
                        {
                            "$group": {
                                "_id": None,
                                "deckCount": {"$sum": 1},
                                "battleCount": {"$sum": "$battleCount"},
                                "wins": {"$sum": "$wins"},
                            }
                        },
                    ],
                    "all": [
                        {
                            "$group": {
                                "_id": None,
                                "totalBattles": {"$sum": "$battleCount"},
                            }
                        }
                    ],
                }
            },
        ]

        result = await conn.db.battles.aggregate(pipeline, allowDiskUse=True).to_list(
            length=1
        )
        facets = result[0] if result else {}
        matching = (facets.get("matching") or [{}])[0]
        return {
            "decks": facets.get("decks", []),
            "deckCount": matching.get("deckCount", 0),
            "battleCount": matching.get("battleCount", 0),
            "wins": matching.get("wins", 0),
            "totalBattles": (facets.get("all") or [{}])[0].get("totalBattles", 0),
        }

    except Exception as e:
        print(f"[DB] [ERROR] fetching decks info: {e}")
        raise


async def get_cards_win_percentage(
    conn: MongoConn,
    player_tag: str,
    start: datetime,
    end: datetime,
    game_modes: Optional[Iterable[str]] = None,
    exclude_game_modes: Optional[Iterable[str]] = None,
):
    """
    Fetches a usage and win percentage for every used card for the player in the specified time range.

    Args:
        conn (MongoConn): Active connection to the MongoDB database.
        player_tag (str): The tag of the player whose card statistics are fetched.
        start (datetime): Start of the UTC window (inclusive), timezone-aware.
        end (datetime): End of the UTC window (exclusive), timezone-aware.
        game_modes (Optional[Iterable[str]]): If provided/non-empty, filter to these game modes in which the game happened.
        exclude_game_modes (Optional[Iterable[str]]): If provided/non-empty,
            leave out battles in these modes. Only used without game_modes.

    Returns:
        list: A list of dictionaries containing the card win-rate and usages
    Raises:
        Exception: If there is an error while fetching the battles from the database.
    """

    try:
        await ensure_connected(conn)
        check_valid_time_range(start, end)

        pipeline = [
            match_tag_time_mode_range_stage(
                player_tag, start, end, game_modes, exclude_game_modes
            ),
            extract_deck_stage(player_tag),
            {
                "$facet": {
                    "cards": [
                        {"$unwind": "$deck.cards"},
                        {
                            "$group": {
                                "_id": {
                                    "id": "$deck.cards.id",
                                    "name": "$deck.cards.name",
                                    "evolutionLevel": "$deck.cards.evolutionLevel",
                                },
                                "usage": {"$sum": 1},  # Usage in battle
                                "wins": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$gameResult", "Victory"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "card": "$_id",
                                "usage": 1,
                                "wins": 1,
                                "winRate": {
                                    "$cond": [
                                        {"$eq": ["$usage", 0]},
                                        0,
                                        {
                                            "$multiply": [
                                                {"$divide": ["$wins", "$usage"]},
                                                100,
                                            ]
                                        },
                                    ]
                                },
                            }
                        },
                        {"$sort": {"usage": -1}},
                    ],
                    # Tower troops, counted separately so they do not change
                    # the regular card statistics. A battle without tower data
                    # counts as None (id 0), which keeps the usage rates of all
                    # towers adding up to 100%.
                    "supportCards": [
                        {
                            "$unwind": {
                                "path": "$deck.support",
                                "preserveNullAndEmptyArrays": True,
                            }
                        },
                        {
                            "$group": {
                                # By id only, so a renamed tower does not
                                # split into two rows
                                "_id": {"$ifNull": ["$deck.support.id", 0]},
                                "name": {
                                    "$first": {
                                        "$ifNull": ["$deck.support.name", "None"]
                                    }
                                },
                                "usage": {"$sum": 1},
                                "wins": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$gameResult", "Victory"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "card": {"id": "$_id", "name": "$name"},
                                "usage": 1,
                                "wins": 1,
                                "winRate": {
                                    "$cond": [
                                        {"$eq": ["$usage", 0]},
                                        0,
                                        {
                                            "$multiply": [
                                                {"$divide": ["$wins", "$usage"]},
                                                100,
                                            ]
                                        },
                                    ]
                                },
                            }
                        },
                        {"$sort": {"usage": -1}},
                    ],
                    "meta": [{"$count": "totalBattles"}],
                }
            },
            {
                "$set": {
                    "totalBattles": {"$ifNull": [{"$first": "$meta.totalBattles"}, 0]}
                }
            },
            {
                "$project": {
                    "totalBattles": 1,
                    "cards": "$cards",
                    "supportCards": "$supportCards",
                }
            },
        ]

        res = await conn.db.battles.aggregate(pipeline, allowDiskUse=True).to_list(
            length=1
        )
        if not res:
            return {"cards": [], "supportCards": [], "totalBattles": 0}

        return res[0]

    except Exception as e:
        print(f"[DB] [ERROR] fetching card stats: {e}")
        raise


async def get_daily_stats(
    conn: MongoConn,
    player_tag: str,
    start: datetime,
    end: datetime,
    game_modes: Optional[Iterable[str]] = None,
    timezone: str = "UTC",
    exclude_game_modes: Optional[Iterable[str]] = None,
):
    """
    Fetches combined daily battle statistics for a player within the specified time window.

    Aggregates per calendar day and (optionally) filters battles by given game modes.
    Returned metrics per day include counts for battles, wins, losses, draws,
    crowns for/against, leaked elixir, and computed win rate.

    Args:
        conn (MongoConn): Active connection to the MongoDB database.
        player_tag (str): The tag of the player whose battles are analyzed (e.g., "#YYRJQY28").
        start (datetime): Start of the UTC window (inclusive), timezone-aware.
        end (datetime): End of the UTC window (exclusive), timezone-aware. A
            window that does not start at a local midnight, like a season,
            makes its first and last day partial.
        game_modes (Optional[Iterable[str]]): If provided and non-empty, only battles
            in these modes are included.
        timezone: Timezone into which the battle days will be grouped (default: UTC)
        exclude_game_modes (Optional[Iterable[str]]): If provided/non-empty,
            leave out battles in these modes. Only used without game_modes.
    Returns:
        list: A list of dictionaries containing the players daily statistics
    Raises:
        Exception: If there is an error while querying or aggregating from the database.
    """

    try:
        await ensure_connected(conn)
        check_valid_time_range(start, end)

        pipeline = [
            # The UTC window & mode filter
            match_tag_time_mode_range_stage(
                player_tag, start, end, game_modes, exclude_game_modes
            ),
            #  derive local day, normalize tags, crowns, flags
            {
                "$addFields": {
                    "day": {
                        "$dateTrunc": {
                            "date": "$battleTime",
                            "unit": "day",
                            "timezone": timezone,
                        }
                    },
                    # extract team tags
                    "teamTags": {
                        "$map": {
                            "input": {"$ifNull": ["$team", []]},
                            "as": "t",
                            "in": "$$t.tag",
                        }
                    },
                    # crowns per side: take MAX across players (avoids 2v2 double-count)
                    "crownsForSafe": {
                        "$reduce": {
                            "input": {
                                "$map": {
                                    "input": {"$ifNull": ["$team", []]},
                                    "as": "t",
                                    "in": {"$ifNull": ["$$t.crowns", 0]},
                                }
                            },
                            "initialValue": 0,
                            "in": {
                                "$cond": [
                                    {"$gt": ["$$this", "$$value"]},
                                    "$$this",
                                    "$$value",
                                ]
                            },
                        }
                    },
                    "crownsAgainstSafe": {
                        "$reduce": {
                            "input": {
                                "$map": {
                                    "input": {"$ifNull": ["$opponent", []]},
                                    "as": "o",
                                    "in": {"$ifNull": ["$$o.crowns", 0]},
                                }
                            },
                            "initialValue": 0,
                            "in": {
                                "$cond": [
                                    {"$gt": ["$$this", "$$value"]},
                                    "$$this",
                                    "$$value",
                                ]
                            },
                        }
                    },
                    "isWin": {"$cond": [{"$eq": ["$gameResult", "Victory"]}, 1, 0]},
                    "isLoss": {"$cond": [{"$eq": ["$gameResult", "Defeat"]}, 1, 0]},
                    "isDraw": {"$cond": [{"$eq": ["$gameResult", "Draw"]}, 1, 0]},
                }
            },
            #  Stage B: compute index of reference player (now fields exist)
            {
                "$addFields": {
                    "refIdx": {"$indexOfArray": ["$teamTags", "$referencePlayerTag"]}
                }
            },
            #  Stage C: extract 'player' safely using refIdx
            {
                "$addFields": {
                    "me": {
                        "$cond": [
                            {"$gte": ["$refIdx", 0]},
                            {"$arrayElemAt": [{"$ifNull": ["$team", []]}, "$refIdx"]},
                            None,
                        ]
                    }
                }
            },
            #  Stage D: cast leaked elixir (no $exists inside agg expr)
            {
                "$addFields": {
                    "elixirLeakedSafe": {
                        "$convert": {
                            "input": "$me.elixirLeaked",
                            "to": "double",
                            "onError": 0,
                            "onNull": 0,
                        }
                    }
                }
            },
            #  Group per local day
            {
                "$group": {
                    "_id": "$day",
                    "battles": {"$sum": 1},
                    "victories": {"$sum": "$isWin"},
                    "defeats": {"$sum": "$isLoss"},
                    "draws": {"$sum": "$isDraw"},
                    "crownsFor": {"$sum": "$crownsForSafe"},
                    "crownsAgainst": {"$sum": "$crownsAgainstSafe"},
                    "elixirLeaked": {"$sum": "$elixirLeakedSafe"},
                }
            },
            #  Shape output & winRate
            {
                "$project": {
                    "_id": 0,
                    "date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$_id",
                            "timezone": timezone,
                        }
                    },
                    "battles": 1,
                    "victories": 1,
                    "defeats": 1,
                    "draws": 1,
                    "crownsFor": 1,
                    "crownsAgainst": 1,
                    "elixirLeaked": {"$round": ["$elixirLeaked", 2]},
                    "winRate": {
                        "$cond": [
                            {"$eq": ["$battles", 0]},
                            0,
                            {
                                "$round": [
                                    {
                                        "$multiply": [
                                            {"$divide": ["$victories", "$battles"]},
                                            100,
                                        ]
                                    },
                                    2,
                                ]
                            },
                        ]
                    },
                }
            },
            {"$sort": {"date": 1}},
        ]

        result = await conn.db.battles.aggregate(pipeline, allowDiskUse=True).to_list(
            length=None
        )
        return {
            "daily": result,
            "totalBattles": sum(day["battles"] for day in result),
        }

    except Exception as e:
        print(f"[DB] [ERROR] fetching daily stats: {e}")
        raise
