from datetime import datetime
from typing import Optional, Iterable


def match_tag_before_datetime_stage(player_tag: str, before_datetime: datetime):
    """
    Build a MongoDB $match stage that filters battles for a given player tag
    that happened before a given datetime

    Args:
        player_tag (str): Player tag (e.g., "#YYRJQY28") to match against `referencePlayerTag`.
        before_datetime (datetime.datetime): Beginning of lookup datetime (inclusive).

    Returns:
        dict: An aggregation stage of the form:
              {
                "$match": {
                    "referencePlayerTag": <player_tag>,
                    "battleTime": {"$lt": <end_datetime>}
                }
              }

    Notes: This function is a pure builder and does not execute any database operation
    """

    return {
        # Match the relevant files for the player and the time frame
        "$match": {
            "referencePlayerTag": player_tag,
            "battleTime": {"$lt": before_datetime},
        }
    }


def match_tag_time_mode_range_stage(
    player_tag: str,
    start: datetime,
    end: datetime,
    game_modes: Optional[Iterable[str]] = None,
    exclude_game_modes: Optional[Iterable[str]] = None,
):
    """
    Build a MongoDB $match stage that filters battles for a given player tag
    within a UTC time window based on the given game modes.

    An exclude list keeps every other mode, also ones no catalogue lists yet,
    so "all but one" stays one name long instead of naming every other mode.

    The caller resolves calendar days or a season to the window, so this
    stage stays a plain range on the indexed battleTime.

    Args:
        player_tag (str): Player tag (e.g., "#YYRJQY28") to match against `referencePlayerTag`.
        start (datetime.datetime): Start of the window (inclusive), timezone-aware.
        end (datetime.datetime): End of the window (exclusive), timezone-aware.
        game_modes: Optional iterable of game mode names; if provided and non-empty,
                    the match includes {"gameMode": {"$in": <game_modes>}}.
        exclude_game_modes: Optional iterable of game mode names; if provided and
                    non-empty, the match includes {"gameMode": {"$nin": <names>}}.
                    The caller sends at most one of the two lists.

    Returns:
        dict: An aggregation stage of the form:
              {
                "$match": {
                  "referencePlayerTag": <player_tag>,
                  "battleTime": { "$gte": <start>, "$lt": <end> },
                  "gameMode": {"$in": <game_modes>} (if game modes isn't empty)
                  "gameMode": {"$nin": <exclude_game_modes>} (if those aren't empty)

                }
              }

    Notes: This function is a pure builder and does not execute any database operation
    """

    match = {
        "referencePlayerTag": player_tag,
        "battleTime": {"$gte": start, "$lt": end},
    }

    # Only add the mode filter if provided and non-empty
    if game_modes:
        match["gameMode"] = {"$in": list(game_modes)}
    elif exclude_game_modes:
        match["gameMode"] = {"$nin": list(exclude_game_modes)}

    return {"$match": match}


def extract_deck_stage(player_tag: str):
    """
    Build a MongoDB `$addFields` stage that extracts the given player's deck
    into a normalized `deck` object with the fields `cards` and `support`.

    The team member with `tag == player_tag` is looked up once. Its `cards`
    array is mapped to:
      - `id` (card id; may be absent in some logs)
      - `name` (defaults to "UNKNOWN" if missing)
      - `level` (defaults to 1 if missing)
      - `evolutionLevel` (integer, defaults to 0 if missing, which is the default for non-evolution cards)

    Its `supportCards` array (the tower troop) is mapped to `id` and `name`.
    An absent, null, or empty `supportCards` becomes `[]`, the "None" tower
    category. Every battle should have a tower, so this is only a fallback
    and never stands for Tower Princess.

    Args:
        player_tag (str): The player tag used to select the team member whose
            deck should be extracted. Teammates are never included.

    Returns:
        dict: An aggregation pipeline stage:
              `{ "$addFields": { "deck": {"cards": [...], "support": [...]} } }`,
              suitable for insertion into a larger pipeline.

    Notes: This function is a pure builder and does not execute any database operation
    """

    return {
        "$addFields": {
            "deck": {
                "$let": {
                    "vars": {
                        # The tracked player's team entry (2v2 has two),
                        # or {} when no team member matches
                        "member": {
                            "$ifNull": [
                                {
                                    "$first": {
                                        "$filter": {
                                            "input": "$team",
                                            "as": "m",
                                            "cond": {"$eq": ["$$m.tag", player_tag]},
                                        }
                                    }
                                },
                                {},
                            ]
                        }
                    },
                    "in": {
                        "cards": {
                            "$map": {
                                "input": {"$ifNull": ["$$member.cards", []]},
                                "as": "c",
                                "in": {
                                    "id": "$$c.id",
                                    "name": {"$ifNull": ["$$c.name", "UNKNOWN"]},
                                    "level": {"$ifNull": ["$$c.level", 1]},
                                    "evolutionLevel": {
                                        "$toInt": {"$ifNull": ["$$c.evolutionLevel", 0]}
                                    },
                                },
                            }
                        },
                        "support": {
                            "$map": {
                                "input": {"$ifNull": ["$$member.supportCards", []]},
                                "as": "s",
                                "in": {
                                    "id": "$$s.id",
                                    "name": {"$ifNull": ["$$s.name", "UNKNOWN"]},
                                },
                            }
                        },
                    },
                }
            }
        }
    }


# Tower troop id of decks without tower data
# NOTE Match NO_SUPPORT_ID in the app's helpers/validate.py and the frontend.
NO_SUPPORT_ID = 0


def deck_card_filter_stages(card_filter: dict) -> list:
    """
    Build the stages that filter grouped decks by cards and tower troops.

    They run on the output of the deck `$group`, whose `_id` is the stored
    deck key "<id>-<evolutionLevel>,...|<tower ids>" (build_deck_key in the
    data scraper's clean.py). Its card part already holds the filter's keys,
    so a split gives the set to compare. Filtering the distinct decks instead
    of the battles checks each deck once, after the indexed match on tag, time
    and mode has done the narrowing. A deck has at most 12 cards, so each check
    costs a few set operations on a small array; the `$group` over the battles
    dominates either way.

    Include mode keeps decks with all selected cards and the selected tower
    troop. Match mode keeps decks that share at least one of them and adds
    `matchedCardCount`: the shared cards, plus 1 if the tower troop is one of the
    selected ones. Excluded cards and tower troops drop a deck in both modes.

    Args:
        card_filter (dict): Normalized filter from the app's
            validate_deck_card_filter: "mode", "cards" and "exclude_cards" as
            "<cardId>-<evolutionLevel>", "support_ids" and "exclude_support_ids".

    Returns:
        list: Aggregation stages to insert right after the deck `$group`.

    Notes: This function is a pure builder and does not execute any database operation
    """

    stages = [
        {"$addFields": {"keyParts": {"$split": ["$_id", "|"]}}},
        {
            "$addFields": {
                # Same "<cardId>-<evolutionLevel>" form as the request
                "cardKeys": {
                    "$let": {
                        "vars": {"cards": {"$first": "$keyParts"}},
                        "in": {
                            "$cond": [
                                {"$eq": ["$$cards", ""]},
                                [],
                                {"$split": ["$$cards", ","]},
                            ]
                        },
                    }
                },
                # The first tower troop id, NO_SUPPORT_ID for an empty tower
                # part. A tower stored by name only never equals a requested id.
                "supportId": {
                    "$let": {
                        "vars": {"towers": {"$last": "$keyParts"}},
                        "in": {
                            "$cond": [
                                {"$eq": ["$$towers", ""]},
                                NO_SUPPORT_ID,
                                {
                                    "$convert": {
                                        "input": {
                                            "$first": {"$split": ["$$towers", ","]}
                                        },
                                        "to": "long",
                                        "onError": -1,
                                    }
                                },
                            ]
                        },
                    }
                },
            }
        },
    ]
    conditions = []

    if card_filter["exclude_cards"]:
        conditions.append(
            {
                "$eq": [
                    {
                        "$size": {
                            "$setIntersection": [
                                "$cardKeys",
                                card_filter["exclude_cards"],
                            ]
                        }
                    },
                    0,
                ]
            }
        )
    if card_filter["exclude_support_ids"]:
        conditions.append(
            {"$not": [{"$in": ["$supportId", card_filter["exclude_support_ids"]]}]}
        )

    if card_filter["mode"] == "match":
        stages.append(
            {
                "$addFields": {
                    "matchedCardCount": {
                        "$add": [
                            {
                                "$size": {
                                    "$setIntersection": [
                                        "$cardKeys",
                                        card_filter["cards"],
                                    ]
                                }
                            },
                            (
                                {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$supportId",
                                                card_filter["support_ids"],
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                                if card_filter["support_ids"]
                                else 0
                            ),
                        ]
                    }
                }
            }
        )
        conditions.append({"$gt": ["$matchedCardCount", 0]})
    else:
        if card_filter["cards"]:
            conditions.append({"$setIsSubset": [card_filter["cards"], "$cardKeys"]})
        if card_filter["support_ids"]:
            conditions.append({"$in": ["$supportId", card_filter["support_ids"]]})

    if conditions:
        stages.append({"$match": {"$expr": {"$and": conditions}}})
    return stages
