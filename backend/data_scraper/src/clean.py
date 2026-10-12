from datetime import datetime, timedelta, timezone
import copy
import hashlib
import logging

from bson import ObjectId

logger = logging.getLogger(__name__)

# Stored battle result, from sideA's view. Signed, so the other side's result
# is the negation, which readers apply per document.
# NOTE Match RESULT_WIN / RESULT_DRAW / RESULT_LOSS in mongo/query_utils.py.
RESULT_WIN = 1
RESULT_DRAW = 0
RESULT_LOSS = -1

# Battle level fields that are not stored. The boat battle fields are further
# information of clan wars battles, that are disregarded in this app. Their
# outcome, boatBattleWon, is kept as the result (determine_game_result).
DROPPED_BATTLE_FIELDS = (
    "deckSelection",
    "isHostedMatch",
    "leagueNumber",
    "isLadderTournament",
    "boatBattleSide",
    "boatBattleWon",
    "newTowersDestroyed",
    "prevTowersDestroyed",
    "remainingTowers",
)


def is_valid_battle(battle) -> bool:
    """
    Checks one battle for the fields the cleaning steps require.

    Args:
        battle: One entry of the battle log, after duel rounds are extracted

    Returns:
        bool: True if the battle can be cleaned, False otherwise
    """

    if not isinstance(battle, dict):
        return False
    if not isinstance(battle.get("battleTime"), str):
        return False
    if not all(isinstance(battle.get(f), dict) for f in ("arena", "gameMode")):
        return False
    return _is_valid_side(battle.get("team")) and _is_valid_side(battle.get("opponent"))


def _is_valid_side(players) -> bool:
    # A non-empty list of players, each with a card list and a tag. The tags
    # place the sides and identify the battle (normalize_battle).
    if not isinstance(players, list) or not players:
        return False
    return all(
        isinstance(player, dict)
        and isinstance(player.get("cards"), list)
        and isinstance(player.get("tag"), str)
        and player["tag"]
        for player in players
    )


def adjust_card_levels(cards):
    """
    Adjusts card levels from the old Clash Royale level system to the new level system.

    The old system had different max levels per rarity, while the new system
    normalizes all cards to level 14/15 with different starting levels per rarity.

    Args:
        cards (list): List of card dictionaries to adjust levels for
    """

    # Clash Royale changed the level system, but their API still returns the old
    # levels. Back then all cards started at level 1 and could be leveled up.
    # Maxed out: Level 13 for common cards, level 11 for rare cards, level 8 for epic cards, ...
    # Nowadays everything is capped at level 14 (15) [16], and the different rarities
    # just start off at different starting levels:
    #   common: 1
    #   rare: 3
    #   epic: 6
    #   legendary: 9
    #   champion: 11

    rarity_level_offsets = {
        "common": 0,
        "rare": 2,
        "epic": 5,
        "legendary": 8,
        "champion": 10,
    }

    # Additionally the card level can be higher than the maxLevel specified for the card
    # Clash Royale introduced Level 15 for all cards, without changing the stat in the API response

    # Some modes have no support cards, and the API omits the field then
    for card in cards or []:
        old_card_level = card.get("level")
        rarity = card.get("rarity")
        if not isinstance(old_card_level, int) or rarity not in rarity_level_offsets:
            # A rarity added after this mapping must not block the whole
            # battle. The level stays in the old system until it is added.
            logger.warning(
                "Card %s kept its API level: unknown rarity %r or level %r",
                card.get("name"),
                rarity,
                old_card_level,
            )
            continue

        # Save the level offset to the new system as card level
        card["level"] = old_card_level + rarity_level_offsets[rarity]


def remove_unnecessary_card_fields(cards):
    """
    Removes unnecessary fields from card objects to reduce storage size.

    Keeps essential fields like name and id for card identification while
    removing metadata that can be retrieved from other sources.

    Args:
        cards (list): List of card dictionaries to clean
    """

    # Remove fields that aren't necessary for storing
    # Keep name and id for each card, so that when id should ever change
    # the already stored data can still be connected to the card via the name
    keys_to_remove = [
        "maxLevel",
        "maxEvolutionLevel",
        "rarity",
        "starLevel",
        "elixirCost",
        "iconUrls",
        "used",  # Only a stat for a card when it's a duel
    ]

    for card in cards or []:
        for key in keys_to_remove:
            card.pop(key, None)


def determine_game_result(battle):
    """
    Determines the result of a battle from the team's view.

    Compares the crown count of the team with that of the opponents.
    normalize_battle turns the result into the view of sideA. A boat battle
    takes boatBattleWon instead, the outcome from the log owner's (team's)
    view, attacker or defender. The API gave its winner 3 crowns and the
    loser 0 in every boat battle checked, so the crowns agree, but the
    outcome does not rely on that.

    Args:
        battle (dict): Battle log dictionary containing team and opponent data

    Returns:
        int: RESULT_WIN, RESULT_LOSS or RESULT_DRAW
    """

    won = battle.get("boatBattleWon")
    if isinstance(won, bool):
        return RESULT_WIN if won else RESULT_LOSS

    # Default to zero as crown amount if it can't be found in the json
    own_crowns = battle.get("team")[0].get("crowns", 0)
    opponent_crowns = battle.get("opponent")[0].get("crowns", 0)

    # Check who won
    if own_crowns > opponent_crowns:
        return RESULT_WIN

    if own_crowns < opponent_crowns:
        return RESULT_LOSS

    # Upon same crown amount
    return RESULT_DRAW


def clean_battle_log_list(battle_logs, player_tag):
    """
    Processes and cleans a list of battle logs from the Clash Royale API.

    Converts timestamps, determines game results, removes unnecessary fields
    and normalizes each battle (normalize_battle) to prepare the data for
    database storage. Each battle is cleaned on its own: a malformed entry is
    logged and skipped, so it cannot keep the valid battles of the same log
    from being stored.

    Args:
        battle_logs (list): List of raw battle log dictionaries from the API
        player_tag (str): The player whose battle log this is, for the logs

    Returns:
        list: The cleaned battles, newest first. Best of 3 duels are expanded
            into one battle per round.
    """

    cleaned = []
    for battle in battle_logs:
        try:
            # A best of 3 duel holds every round in one entry
            if battle["team"][0].get("rounds"):
                battles = extract_duel_battles(battle)
            else:
                battles = [battle]
            # A duel is only stored with all of its rounds
            cleaned.extend([_clean_single_battle(b) for b in battles])
        except Exception:
            battle_time = battle.get("battleTime") if isinstance(battle, dict) else None
            logger.exception(
                "Skipped malformed battle of %s at %s", player_tag, battle_time
            )

    return cleaned


def _clean_single_battle(battle):
    """
    Cleans and normalizes one battle (or one extracted duel round) in place.

    Raises:
        ValueError: If the battle lacks a field the cleaning requires.
    """

    if not is_valid_battle(battle):
        raise ValueError("Battle is missing required fields")

    # e.g. "20250817T022935.000Z"
    battle["battleTime"] = datetime.strptime(battle["battleTime"], "%Y%m%dT%H%M%S.000Z")

    # Both run while the boat battle fields are still there
    clean_battle(battle)
    battle["result"] = determine_game_result(battle)

    for key in DROPPED_BATTLE_FIELDS:
        battle.pop(key, None)

    # Refactor Arena and GameMode (get rid of id)
    battle["arena"] = battle["arena"].get("name")
    battle["gameMode"] = battle["gameMode"].get("name")

    return normalize_battle(battle)


def normalize_battle(battle):
    """
    Turns a battle from one player's view into the stored, view free form.

    Every participant's battle log holds the same battle, with the own side
    as team. Sorting the players of each side by tag, and calling the side
    with the smallest tag sideA, gives every copy the same layout, so the
    copies share one _id and the battle is stored once (see
    split_battle_copies in mongo/battles_read.py for copies whose times
    differ). Works in 2v2 as well:
    all four logs agree on the order. Readers turn it back into one player's
    view with perspective_stages (mongo/query_utils.py).

    NOTE One document per battle instead of one per tracked participant
    keeps statistics across all battles honest: a meta analysis or a deck
    and card usage estimate would otherwise count every battle between two
    tracked players twice, and these are the battles most likely to be
    stored. Especially relevant when tracked friends, clanmates or top
    players often play with (TeamVsTeam) or against each other. playerTags
    holds every participant, not only the tracked ones, so a player tracked
    later already has the battles against players tracked before, and is
    passively tracked (to a certain degree) through them until then.

    Sets:
      - sideA, sideB: the sides, players sorted by tag, each with deckKey
      - result: from sideA's view (RESULT_WIN, RESULT_LOSS or RESULT_DRAW)
      - playerTags: every participant, sideA first, for the player index
      - _id: battle_id

    Args:
        battle (dict): Cleaned battle with team, opponent, result from the
            team's view and battleTime as datetime. Every player has a tag.

    Returns:
        dict: The same battle, normalized in place.
    """

    team = sorted(battle.pop("team"), key=lambda player: player["tag"])
    opponent = sorted(battle.pop("opponent"), key=lambda player: player["tag"])
    result = battle.pop("result")

    if opponent[0]["tag"] < team[0]["tag"]:
        team, opponent = opponent, team
        result = -result

    for player in team + opponent:
        player["deckKey"] = build_deck_key(player)

    battle["sideA"] = team
    battle["sideB"] = opponent
    battle["result"] = result
    battle["playerTags"] = [player["tag"] for player in team + opponent]
    battle["_id"] = battle_id(battle["battleTime"], team + opponent)
    return battle


def battle_id(battle_time, players):
    """
    Builds the _id of a battle from its time, participants and decks.

    One player cannot be in two battles at once, but one boat defense can be
    attacked by several players at the same time, so the time and a single
    tag are not unique. All tags are. The decks are part of it as well, so
    that two ids are only equal for the same battle: duel rounds are one
    second apart, and in a log shifted by a second one round would otherwise
    get the id of another. A duplicate key on insert therefore always means
    a copy. The id has the layout of an ObjectId, 4 bytes of seconds and 8
    bytes of a hash of the sorted tags and decks, so it costs the _id index
    no more than one, and new battles are added at its end.

    Two logs can report the same battle one second apart, which gives the
    copies different ids. Those are caught before the insert by
    split_battle_copies (mongo/battles_read.py); the id only catches exact
    copies, including two syncs inserting the same battle at once.

    Args:
        battle_time (datetime): Battle time, UTC (naive, as stored)
        players (list[dict]): Every participant with tag and deckKey, in any
            order

    Returns:
        ObjectId: The id, its generation_time is the battle time
    """

    seconds = int(battle_time.replace(tzinfo=timezone.utc).timestamp())
    identity = "|".join(sorted(f"{p['tag']}={p['deckKey']}" for p in players))
    digest = hashlib.blake2b(identity.encode(), digest_size=8).digest()
    return ObjectId(seconds.to_bytes(4, "big") + digest)


def build_deck_key(player):
    """
    Builds the identity of one participant's deck as one string.

    The deck statistics group battles by it, so they need not extract and sort
    every battle's cards at read time. Card levels stay out: a deck is the
    same deck at any level. Its parts are already the card filter's keys.

    NOTE The format is parsed by deck_card_filter_stages (mongo/query_utils.py)
    and has to stay identical to what the deck statistics expect:
    "<id>-<evolutionLevel>,..." sorted as strings, "|", then the tower troop
    ids sorted as strings, e.g. "26000000-1,26000010-0,...|159000000". A deck
    without a tower troop ends in "|". A card without an id uses its name.

    Args:
        player (dict): Cleaned participant, evolution levels as stored

    Returns:
        str: The deck key, "|" for a player without cards
    """

    # Should always use the id for the key, fallback to name if no id is present.
    # This is (hopefully) redundant and Clash Royale (hopefully) will always
    # provide a stable id for the same card.
    def identity(card):
        return card.get("id") if card.get("id") is not None else card.get("name")

    cards = sorted(
        f"{identity(card)}-{int(card.get('evolutionLevel') or 0)}"
        for card in player.get("cards") or []
    )
    towers = sorted(str(identity(tower)) for tower in player.get("supportCards") or [])
    return ",".join(cards) + "|" + ",".join(towers)


def extract_duel_battles(battle):
    """
    Extracts individual battles from a best-of-3 duel battle log.

    Each duel in the Clash Royale API is represented as a single battle entry
    containing multiple "rounds". This function expands that combined entry into
    separate battle dictionaries, one per round, by copying the original structure,
    replacing the per-round values (cards, crowns, towers, elixir), and adjusting
    the battle timestamp to be unique for each round by a one second offset per round.

    Args:
        battle (dict): A raw duel battle log dictionary from the Clash Royale API.

    Returns:
        list: A list of battle dictionaries, one for each round of the duel.
    """

    extracted = []

    team_list = battle.get("team") or []
    opp_list = battle.get("opponent") or []

    # handle empty/odd cases
    team_player = team_list[0] if team_list else {}
    opp_player = opp_list[0] if opp_list else {}

    team_rounds = team_player.get("rounds") or []
    opp_rounds = opp_player.get("rounds") or []

    # iterate only over rounds that exist on both sides
    # should always be same amount
    round_count = min(len(team_rounds), len(opp_rounds))
    for i in range(round_count):
        team = team_rounds[i]
        opp = opp_rounds[i]
        current_battle = copy.deepcopy(battle)

        # ensure lists exist and have at least one player
        if not current_battle.get("team"):
            current_battle["team"] = [{}]
        if not current_battle.get("opponent"):
            current_battle["opponent"] = [{}]

        # write per-round values onto player #0
        team0 = current_battle["team"][0]
        opp0 = current_battle["opponent"][0]

        # TEAM per-round
        team0["cards"] = team.get("cards")
        team0["crowns"] = team.get("crowns")
        team0["kingTowerHitPoints"] = team.get("kingTowerHitPoints")
        team0["princessTowersHitPoints"] = team.get("princessTowersHitPoints")
        team0["elixirLeaked"] = team.get("elixirLeaked")
        # A round's own tower troop, if the log has one per round, even an
        # empty one (None). Only an absent field keeps the duel-level tower.
        if "supportCards" in team:
            team0["supportCards"] = team["supportCards"]
        team0.pop("rounds")  # Remove the rounds list from the current battle

        # OPPONENT per-round
        opp0["cards"] = opp.get("cards")
        opp0["crowns"] = opp.get("crowns")
        opp0["kingTowerHitPoints"] = opp.get("kingTowerHitPoints")
        opp0["princessTowersHitPoints"] = opp.get("princessTowersHitPoints")
        opp0["elixirLeaked"] = opp.get("elixirLeaked")
        if "supportCards" in opp:
            opp0["supportCards"] = opp["supportCards"]
        opp0.pop("rounds")  # Remove the rounds list from the current battle

        # The API reports trophyChange once for the whole duel. Only the last
        # round keeps it, where the duel is decided, so no earlier round
        # carries a change its own crowns may contradict.
        if i < round_count - 1:
            for player in current_battle["team"] + current_battle["opponent"]:
                player.pop("trophyChange", None)

        battle_time_str = current_battle.get("battleTime")
        dt = datetime.strptime(battle_time_str, "%Y%m%dT%H%M%S.000Z")

        # Shift by i seconds, so the position in the duel
        dt_shifted = dt + timedelta(seconds=i)
        current_battle["battleTime"] = dt_shifted.strftime("%Y%m%dT%H%M%S.000Z")

        extracted.append(current_battle)

    return extracted


def clean_battle(battle):
    """
    Cleans individual battle data by processing player information.

    Removes unnecessary player fields, adjusts card levels, and cleans up
    card data for both team and opponent players in the battle.

    Args:
        battle (dict): Single battle log dictionary to clean
    """

    team = battle.get("team")
    opponent = battle.get("opponent")

    # Loop over every player in the battle: team player(s) and opponent player(s)
    for player in team + opponent:
        # Remove the clan for each player
        player.pop("clan", None)

        # Clean and adjust the players deck cards
        adjust_card_levels(player.get("cards"))
        remove_unnecessary_card_fields(player.get("cards"))

        # Clean and adjust the players support card(s) [Tower Troop]
        adjust_card_levels(player.get("supportCards"))
        remove_unnecessary_card_fields(player.get("supportCards"))

        player.pop("globalRank", None)

    remove_boat_defense_evolutions(battle)


def remove_boat_defense_evolutions(battle):
    """
    Drops the evolution levels of a ClanWar_BoatBattle defense.

    The API reports them on the 12 defense cards, but they follow the
    defender's unlocked variants (1 evolution, 2 hero, 3 both) instead of
    anything played, so they would split identical defenses and invent a
    level 3 that no card has. Only works before normalization: stored
    battles have neither boatBattleSide nor a team.

    NOTE Assumes evolutions never take part in a boat defense, which held for
    every battle checked so far. If the game ever lets them, this rule has to
    go, although no focus currently lays on any clan wars battles.

    Args:
        battle (dict): Battle with team, opponent, gameMode (dict or name)
            and boatBattleSide, the side of the player whose log it is

    Returns:
        bool: True if the battle is a boat battle and a defense was cleaned
    """

    game_mode = battle.get("gameMode")
    if isinstance(game_mode, dict):
        game_mode = game_mode.get("name")
    if game_mode != "ClanWar_BoatBattle":
        return False

    # The team is always the side of the player whose log it is
    side = battle.get("boatBattleSide")
    if side == "defender":
        defenders = battle.get("team") or []
    elif side == "attacker":
        defenders = battle.get("opponent") or []
    else:
        logger.warning("Boat battle without a known side: %r", side)
        return False

    for player in defenders:
        for card in player.get("cards") or []:
            card.pop("evolutionLevel", None)
    return True


def get_player_name(battles, player_tag):
    """
    Extracts the player's name from the most recent battle log entry.

    Args:
        battles (list[dict]): Cleaned battles (newest first), see clean_battle_log_list.
        player_tag (str): The tag of the player whose name is being retrieved.

    Returns:
        str: The player's name if found, otherwise the given `player_tag`.
    """

    # battles[0] is the newest battle, so chances are this is the actual current name.
    # Easiest way to get the actual name would be to use the get_player_info of the clash_royale_api module,
    # but extracting it from the battle log, which is already being fetched, saves one API-call per cycle per player
    battle = battles[0]  # Take first battle of battles in the list

    # Normalized, so the player can be on either side
    for player in battle["sideA"] + battle["sideB"]:
        if player.get("tag") == player_tag:
            return player.get("name")

    return player_tag  # Default to the tag if the name couldn't be found
