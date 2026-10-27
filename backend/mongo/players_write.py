from datetime import datetime, timezone
from pymongo import ReturnDocument, UpdateMany, UpdateOne
from .connection import MongoConn
from .players_read import LISTED_PLAYER_FILTER, NOT_PROTECTED, PARKED_REASON
from .validation_utils import ensure_connected

# NOTE searchChangedAt tells the API's player search which players to read
# again (see app/src/player_search/sync.py). Every write that changes
# playerName or active HAS to set it to the server time ($$NOW or
# $currentDate), or the search keeps the old state until its next full
# refresh. Server time, not this process's clock: the search compares it
# with Mongo's own clock.


def stored_time_now() -> str:
    """The current time as the player document stores it, in UTC.

    The readers (tracking gaps, the pause start) take these strings as UTC,
    so the local time of a process outside a UTC container would shift them.

    TODO Store insertedAt, deactivatedAt, reactivatedAt and the trackingGaps
    bounds as BSON dates like every newer field, with a migration that
    converts the existing strings and readers that accept both meanwhile.

    Returns:
        str: "YYYY-MM-DD HH-MM-SS"
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H-%M-%S")


def name_update_fields(player_name: str) -> dict:
    """Return the $set fields of a pipeline update that stores a player name.

    searchChangedAt only moves when the name actually differs. Syncs store
    the name on every run, and bumping it each time would make the search
    re-read every synced player.

    Args:
        player_name (str): The current player name.

    Returns:
        dict: Fields for a pipeline $set stage, not for a plain $set.
    """

    # $literal, because a name starting with "$" would otherwise be read
    # as a field path. Both expressions see the document as it was before
    # this stage, so the comparison is with the stored name.
    name = {"$literal": player_name}
    return {
        "playerName": name,
        "searchChangedAt": {
            "$cond": [{"$ne": ["$playerName", name]}, "$$NOW", "$searchChangedAt"]
        },
    }


async def ensure_search_change_index(conn: MongoConn) -> None:
    """Create the index the search's change reads use, if missing.

    Created at startup as well as in the init script: the init script only
    runs on an empty volume, so existing databases get it here.

    Args:
        conn (MongoConn): Active MongoDB connection instance.

    Raises:
        Exception: If the index cannot be created.
    """

    try:
        await ensure_connected(conn)
        await conn.db.players.create_index(
            "searchChangedAt", name="searchChangedAt_index"
        )
    except Exception as e:
        print(f"[DB] [ERROR] creating the searchChangedAt index: {e}")
        raise


def reactivation_stages(now: str, extra_fields: dict | None = None) -> list[dict]:
    """Pipeline stages that track an inactive player again.

    Shared by the add route and the top players rollover, so both record the
    untracked period the same way.

    Args:
        now (str): The current time as stored_time_now() returns it.
        extra_fields (dict | None): More fields for the $set stage.

    Returns:
        list[dict]: Stages for a pipeline update of an inactive player.
    """

    return [
        {
            "$set": {
                "active": True,
                # Starts the parking spare window (get_park_candidates)
                "activatedAt": "$$NOW",
                "reactivatedAt": now,
                "updatedAt": now,
                "searchChangedAt": "$$NOW",
                "consecutiveFailures": 0,
                "consecutiveNotFound": 0,
                # Battles played while untracked are only recovered if they
                # still fit into the battle log, so every untracked period is
                # kept for the player page.
                "trackingGaps": {
                    "$concatArrays": [
                        {"$ifNull": ["$trackingGaps", []]},
                        [
                            {
                                "from": "$deactivatedAt",
                                "to": now,
                                # Set by the next sync with battles
                                "ownBattlesFrom": None,
                            }
                        ],
                    ]
                },
                **(extra_fields or {}),
            }
        },
        # A player deactivated after repeated 404s exists again, so its old
        # not-found streak must not count towards a new one. The adaptive
        # intervals describe the old session; a stretched one would delay the
        # first refreshes of the new session.
        {
            "$unset": [
                "deactivatedReason",
                "firstNotFoundAt",
                "syncIntervalS",
                "profileSyncIntervalS",
                "lastSyncNewBattles",
            ]
        },
    ]


def new_player_fields(now: str, player_name: str) -> dict:
    """Fields of a newly inserted player document, for $setOnInsert.

    Args:
        now (str): The current time as stored_time_now() returns it.
        player_name (str): The player's name.

    Returns:
        dict: Everything but playerTag, active, regular and searchChangedAt.
    """

    return {
        "insertedAt": now,
        "activatedAt": datetime.now(timezone.utc),
        # Unviewed players sort as 0 in the parking order from the start, not
        # before every counted 0 as a missing field would.
        "views30d": 0,
        "playerName": player_name,
        "syncVersion": 0,
        # Set by the first sync with battles
        "ownBattlesFrom": None,
    }


async def insert_tracked_player(
    conn: MongoConn, player_tag: str, player_name: str = "Player"
) -> str:
    """
    Insert (or reactivate) a player in the `players` collection.

    - If the player doesn't exist: create with active=True.
    - If the player exists: set active=True again (reactivate).

    Either way the player becomes regular: someone wants it tracked for its
    own sake, so it stays tracked after a season in the top players ends.

    A new player is stored with ownBattlesFrom = None, and a reactivated one
    gets it on its new tracking gap. The first sync after that sets it to the
    oldest battle of the player's own log (record_battle_sync). Battles
    stored before it, or during the gap before it, came from other tracked
    players the player played with or against, and are only a part of what
    it played then (count_battles_in_ranges). A time rather than a count, so
    the number follows the stored battles once old ones are removed.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The unique tag of the player (e.g., "#YYRJQY28").
        player_name (str): The name to set for the player (default: "Player").

    Returns:
        str: "created", "reactivated", or "already_tracked"
    """
    try:
        await ensure_connected(conn)
        now = stored_time_now()

        # Try to reactivate if it exists but is inactive. A pipeline update,
        # so the gap can be built from the stored deactivatedAt in the same
        # atomic write.
        res = await conn.db.players.update_one(
            {"playerTag": player_tag, "active": False},
            reactivation_stages(now, {"regular": True}),
            upsert=False,
        )
        if res.matched_count == 1:
            return "reactivated"

        # If not matched above, upsert (create if missing, otherwise just ensure active)
        res = await conn.db.players.update_one(
            {"playerTag": player_tag},
            {
                "$set": {
                    "playerTag": player_tag,
                    "active": True,
                    "regular": True,
                    "updatedAt": now,
                },
                "$setOnInsert": new_player_fields(now, player_name),
                # Also bumped for an already tracked player, which is harmless:
                # the search re-reads the player and finds nothing changed.
                # The match may also be a player deactivated between these two
                # writes, which this reactivates, so it has to be set here.
                "$currentDate": {"searchChangedAt": True},
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
    conn: MongoConn,
    player_tag,
    reason: str | None = None,
    include_protected: bool = False,
) -> int:
    """
    Deactivates a player that is being tracked into the players collection.

    A user removal (no reason) also clears regular: nobody wants the player
    tracked anymore, so a later top season does not keep it past its end.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")
        reason (str | None): Stored as deactivatedReason when the system, not
            a user, untracks the player (e.g. "not_found")
        include_protected (bool): Also deactivate a top player (topFinish).
            Only for players that are gone; parking and removals leave them,
            even if topFinish was set after the caller read the player.

    Returns:
        int: The amount of affected players by the update

    Raises:
        Exception: If the update of the player fails
    """

    try:
        await ensure_connected(conn)
        current_time = stored_time_now()

        fields = {"active": False, "deactivatedAt": current_time}
        if reason:
            fields["deactivatedReason"] = reason
        else:
            fields["regular"] = False

        query = {"playerTag": player_tag, "active": True}
        if not include_protected:
            query.update(NOT_PROTECTED)
        res = await conn.db.players.update_one(
            query, {"$set": fields, "$currentDate": {"searchChangedAt": True}}
        )

        # Return the amount of players updated
        return res.matched_count

    except Exception as e:
        print(f"[DB] [ERROR] during update: {e}")
        raise


async def remove_parked_player(conn: MongoConn, player_tag: str) -> int:
    """
    Turns a parked player into a removed one, which the search no longer lists.

    A parked top player keeps its protection until the next season, so it is
    left as it is.

    deactivatedAt keeps the time of parking: the player has not been synced
    since then, so a later reactivation records the gap from there.

    Args:
        conn (MongoConn): Active connection to the mongo database
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        int: 1 if the player was parked, otherwise 0

    Raises:
        Exception: If the update of the player fails
    """

    try:
        await ensure_connected(conn)
        res = await conn.db.players.update_one(
            {
                "playerTag": player_tag,
                "active": False,
                "deactivatedReason": PARKED_REASON,
                **NOT_PROTECTED,
            },
            {
                "$set": {"regular": False},
                "$unset": {"deactivatedReason": ""},
                "$currentDate": {"searchChangedAt": True},
            },
        )
        return res.matched_count

    except Exception as e:
        print(f"[DB] [ERROR] removing the parked player {player_tag}: {e}")
        raise


async def record_battle_sync(
    conn: MongoConn,
    player_tag: str,
    newest_battle_time: datetime | None,
    player_name: str | None,
    new_battle_count: int,
    interval_s: float | None = None,
    oldest_battle_time: datetime | None = None,
):
    """
    Stores the outcome of a successful battle sync on the player document.

    syncVersion is only incremented when the sync found new battles, or when
    another participant's sync stored battles of the player
    (bump_sync_versions). Cached statistics of a player without new battles
    therefore stay valid.

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
        oldest_battle_time (datetime | None): battleTime of the oldest battle
            in the fetched battle log, None if the log was empty. Fills a
            pending (None) ownBattlesFrom of the player and of its newest
            tracking gap, see insert_tracked_player.

    Raises:
        Exception: Any exception that occurs during the database update operation.
    """

    try:
        await ensure_connected(conn)

        # A pipeline update, so the name can be compared with the stored one
        # in the same write (see name_update_fields).
        fields = {
            "lastBattlesSyncAt": datetime.now(timezone.utc),
            "lastSyncNewBattles": new_battle_count,
            "consecutiveFailures": 0,
            "consecutiveNotFound": 0,
        }
        if player_name:
            fields.update(name_update_fields(player_name))
        if interval_s is not None:
            fields["syncIntervalS"] = interval_s
        if newest_battle_time is not None:
            # $max keeps the watermark from moving backwards if two syncs of
            # the same player overlap after an expired claim. It skips a
            # missing field, so the first sync stores the new time.
            fields["lastBattleTime"] = {"$max": ["$lastBattleTime", newest_battle_time]}
        if oldest_battle_time is not None:
            fields.update(own_battles_fields(oldest_battle_time))
        # TODO Keep a roughly accurate stored battle count per tracked player
        # (e.g. battleCount), so pages and the explore list need no count over
        # the battles collection. Not per insert: either $inc it here by the
        # sync's new battles (one write per sync, already batched), or let a
        # periodic job recount from the playerTags index. The $inc
        # drifts, e.g. when two overlapping syncs after an expired claim both
        # count the same battles, or when battles are removed later, so it
        # could be combined with a rare recount. "Somewhat accurate" is
        # enough; store when it was last counted (battleCountAt) next to it.
        if new_battle_count > 0:
            fields["syncVersion"] = {"$add": [{"$ifNull": ["$syncVersion", 0]}, 1]}

        await conn.db.players.update_one(
            {"playerTag": player_tag},
            [{"$set": fields}, {"$unset": ["firstNotFoundAt"]}],
            upsert=False,
        )

    except Exception as e:
        print(f"[DB] [ERROR] recording the battle sync of {player_tag}", e)
        raise


def own_battles_fields(oldest_battle_time: datetime) -> dict:
    """
    Pipeline $set fields that fill pending ownBattlesFrom values.

    Only None means pending. An absent field (players and gaps from before
    it was recorded) stays absent: the oldest battle of a later log would
    declare all older battles passive. A set time is never moved. Only the
    newest gap can be the one the player just returned from.

    Args:
        oldest_battle_time (datetime): Oldest battle of the fetched log.

    Returns:
        dict: Fields for the $set stage of record_battle_sync.
    """

    def filled(value):
        # "$..." of an absent field evaluates to missing, so $set leaves it out
        return {
            "$cond": [{"$eq": [{"$type": value}, "null"]}, oldest_battle_time, value]
        }

    last = {"$subtract": [{"$size": "$trackingGaps"}, 1]}
    return {
        "ownBattlesFrom": filled("$ownBattlesFrom"),
        "trackingGaps": {
            "$cond": [
                {"$isArray": "$trackingGaps"},
                {
                    "$map": {
                        "input": {"$range": [0, {"$size": "$trackingGaps"}]},
                        "as": "i",
                        "in": {
                            "$let": {
                                "vars": {
                                    "gap": {"$arrayElemAt": ["$trackingGaps", "$$i"]}
                                },
                                "in": {
                                    "$cond": [
                                        {"$eq": ["$$i", last]},
                                        {
                                            "$mergeObjects": [
                                                "$$gap",
                                                {
                                                    "ownBattlesFrom": filled(
                                                        "$$gap.ownBattlesFrom"
                                                    )
                                                },
                                            ]
                                        },
                                        "$$gap",
                                    ]
                                },
                            }
                        },
                    }
                },
                "$trackingGaps",
            ]
        },
    }


async def bump_sync_versions(conn: MongoConn, player_tags) -> None:
    """
    Increments syncVersion of players whose battles another sync stored.

    A battle is stored once for all participants, so one player's sync can
    add battles of another tracked player, e.g. from before that player was
    tracked or past an overflow of its own log. Its own sync never sees
    those as new, so without this the cached statistics would keep missing
    them until their TTL ends. Tags without a player document are skipped.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tags (Iterable[str]): Tags of the other participants of the
            newly stored battles.

    Raises:
        Exception: Any exception that occurs during the database update operation.
    """

    tags = list(set(player_tags))
    if not tags:
        return
    try:
        await ensure_connected(conn)
        # $inc starts a missing syncVersion at 1, like record_battle_sync
        await conn.db.players.update_many(
            {"playerTag": {"$in": tags}}, {"$inc": {"syncVersion": 1}}
        )
    except Exception as e:
        print(f"[DB] [ERROR] bumping the sync versions of {len(tags)} players", e)
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


async def migrate_regular_players(conn: MongoConn) -> int:
    """Mark every tracked and parked player without the field as regular.

    Before the top players, every tracked player was there for its own sake.
    Only players lacking the field are touched, so this runs at every start:
    players inserted from a leaderboard store regular=False, and removed
    players are neither tracked nor parked. The rollover reads regular to
    decide whom to park when a season's top list changes, so it runs first.

    Args:
        conn (MongoConn): Active MongoDB connection instance.

    Returns:
        int: Number of players marked.

    Raises:
        Exception: If the update fails.
    """

    try:
        await ensure_connected(conn)
        res = await conn.db.players.update_many(
            {**LISTED_PLAYER_FILTER, "regular": {"$exists": False}},
            {"$set": {"regular": True}},
        )
        return res.modified_count
    except Exception as e:
        print(f"[DB] [ERROR] marking the existing players as regular: {e}")
        raise


async def set_player_regular(conn: MongoConn, player_tag: str) -> int:
    """Keep a tracked player after its season in the top players ends.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        int: 1 if the player is tracked, otherwise 0 (nothing changed).

    Raises:
        Exception: If the update fails.
    """

    try:
        await ensure_connected(conn)
        res = await conn.db.players.update_one(
            {"playerTag": player_tag, "active": True}, {"$set": {"regular": True}}
        )
        return res.matched_count
    except Exception as e:
        print(f"[DB] [ERROR] marking {player_tag} as regular: {e}")
        raise


async def write_top_finish(
    conn: MongoConn, season: str, players: list[dict]
) -> list[str]:
    """Protect and track the top players of a season.

    Every listed player gets topFinish first, so it is protected before it is
    tracked and an hourly parking check in between cannot take it again.
    New tags are inserted with the leaderboard name, which spends no Clash
    Royale request, and regular=False. Inactive ones are tracked again with a
    tracking gap, like insert_tracked_player. Safe to repeat: a second run
    finds every player tracked and changes nothing but topFinish.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        season (str): Season id of the leaderboard, as "YYYY-MM".
        players (list[dict]): {"tag", "name", "rank"} per listed player.

    Returns:
        list[str]: Tags this call started tracking, inserted or reactivated.

    Raises:
        Exception: If a write fails. Writes done before it stay.
    """

    try:
        await ensure_connected(conn)
        now = stored_time_now()
        ops = [
            UpdateOne(
                {"playerTag": p["tag"]},
                {
                    "$set": {"topFinish": {"season": season, "rank": p["rank"]}},
                    "$setOnInsert": {
                        **new_player_fields(now, p["name"]),
                        "active": True,
                        "regular": False,
                        "updatedAt": now,
                    },
                },
                upsert=True,
            )
            for p in players
        ]
        inserted = []
        for start in range(0, len(ops), RANKING_BATCH_SIZE):
            batch = ops[start : start + RANKING_BATCH_SIZE]
            res = await conn.db.players.bulk_write(batch, ordered=False)
            inserted += [players[start + i]["tag"] for i in res.upserted_ids]

        # By tag too: after a smaller TOP_PLAYERS_N, players no longer listed
        # keep this season's topFinish until the rollover retires them.
        listed = {
            "topFinish.season": season,
            "playerTag": {"$in": [p["tag"] for p in players]},
        }
        # Only players inserted without it: $currentDate in the upserts would
        # make the search re-read every listed player. Matched by the missing
        # field, not by this call's inserts, so a repeat after a failure
        # between both writes still marks the players the failed call added.
        await conn.db.players.update_many(
            {**listed, "searchChangedAt": {"$exists": False}},
            {"$currentDate": {"searchChangedAt": True}},
        )
        # Removed players never got the field. Without it, the next start's
        # migration would count a reactivated one as regular.
        await conn.db.players.update_many(
            {**listed, "regular": {"$exists": False}}, {"$set": {"regular": False}}
        )

        inactive = {**listed, "active": False}
        reactivated = [
            doc["playerTag"]
            async for doc in conn.db.players.find(inactive, {"_id": 0, "playerTag": 1})
        ]
        if reactivated:
            await conn.db.players.update_many(
                {**inactive, "playerTag": {"$in": reactivated}},
                reactivation_stages(now),
            )
        return inserted + reactivated

    except Exception as e:
        print(f"[DB] [ERROR] writing the top players of {season}: {e}")
        raise


async def retire_former_top_player(conn: MongoConn, player_tag: str) -> bool:
    """Drop the protection of a top player the current top list leaves out.

    A regular player stays tracked. Any other is parked, so its page and its
    place in the search stay and anyone can track it again. One atomic write:
    a "Move to regular" right before it lands first and keeps the player, one
    right after finds it parked and tracks it again. The caller removes a
    parked player's schedules right after, as with any parking. Only the
    rollover writes topFinish, so a tag from get_former_top_players of the
    same pass is still left out when this runs.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        player_tag (str): The player tag starting with '#' (e.g., "#YYRJQY28")

    Returns:
        bool: True if this call parked the player.

    Raises:
        Exception: If a write fails.
    """

    try:
        await ensure_connected(conn)
        former = {"playerTag": player_tag, "topFinish": {"$exists": True}}
        res = await conn.db.players.update_one(
            {**former, "active": True, "regular": {"$ne": True}},
            {
                "$set": {
                    "active": False,
                    "deactivatedAt": stored_time_now(),
                    "deactivatedReason": PARKED_REASON,
                },
                "$unset": {"topFinish": ""},
                "$currentDate": {"searchChangedAt": True},
            },
        )
        if res.matched_count:
            return True
        # Regular, or already off the schedules (e.g. its account is gone)
        await conn.db.players.update_one(former, {"$unset": {"topFinish": ""}})
        return False

    except Exception as e:
        print(f"[DB] [ERROR] retiring the former top player {player_tag}: {e}")
        raise


# Writes per bulk_write call of the ranking, so one call stays well below
# Mongo's message size limit.
RANKING_BATCH_SIZE = 1000


async def write_view_ranking(
    conn: MongoConn, views: dict[str, int], min_views: int
) -> int:
    """
    Stores the windowed view counts and the explore ranking on the players.

    Every viewed player gets views30d. A player without views in the window
    goes back to 0, and so does one without the field yet. exploreRank then numbers the active players with at least
    min_views by views, most first, tag as the tiebreaker, leaving out
    players with exploreHidden. Everyone else loses the field. The ranking
    job is the only writer of both fields.

    Args:
        conn (MongoConn): Active MongoDB connection instance.
        views (dict[str, int]): Player tag mapped to its daily visitors summed
            over the window.
        min_views (int): Fewest of those to be ranked.

    Returns:
        int: Number of ranked players.

    Raises:
        Exception: Any exception that occurs during the database operations.
    """

    try:
        await ensure_connected(conn)
        players = conn.db.players

        previous = [
            doc["playerTag"]
            async for doc in players.find(
                {"views30d": {"$gt": 0}}, {"_id": 0, "playerTag": 1}
            )
        ]
        ops = [
            UpdateOne({"playerTag": tag}, {"$set": {"views30d": count}})
            for tag, count in views.items()
        ]
        dropped = [tag for tag in previous if tag not in views]
        if dropped:
            ops.append(
                UpdateMany({"playerTag": {"$in": dropped}}, {"$set": {"views30d": 0}})
            )
        # Players inserted before the add route stored a 0, or without the
        # API. A missing field would sort before every counted 0 when parking.
        ops.append(
            UpdateMany({"views30d": {"$exists": False}}, {"$set": {"views30d": 0}})
        )
        await _bulk_write_batches(players, ops)

        ranked = [
            doc["playerTag"]
            async for doc in players.find(
                {
                    "active": True,
                    "views30d": {"$gte": min_views},
                    "exploreHidden": {"$ne": True},
                },
                {"_id": 0, "playerTag": 1},
            ).sort([("views30d", -1), ("playerTag", 1)])
        ]
        ranked_tags = set(ranked)
        unranked = [
            doc["playerTag"]
            async for doc in players.find(
                {"exploreRank": {"$exists": True}}, {"_id": 0, "playerTag": 1}
            )
            if doc["playerTag"] not in ranked_tags
        ]
        ops = [
            UpdateOne({"playerTag": tag}, {"$set": {"exploreRank": rank}})
            for rank, tag in enumerate(ranked, start=1)
        ]
        if unranked:
            ops.append(
                UpdateMany(
                    {"playerTag": {"$in": unranked}}, {"$unset": {"exploreRank": ""}}
                )
            )
        await _bulk_write_batches(players, ops)
        return len(ranked)

    except Exception as e:
        print(f"[DB] [ERROR] writing the view ranking: {e}")
        raise


async def _bulk_write_batches(collection, ops: list) -> None:
    """Run unordered bulk writes in batches of RANKING_BATCH_SIZE."""

    for start in range(0, len(ops), RANKING_BATCH_SIZE):
        await collection.bulk_write(
            ops[start : start + RANKING_BATCH_SIZE], ordered=False
        )
