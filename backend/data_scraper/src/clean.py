from datetime import datetime, timedelta
import copy
import logging

logger = logging.getLogger(__name__)


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
    # A non-empty list of players, each with a card list
    if not isinstance(players, list) or not players:
        return False
    return all(
        isinstance(player, dict) and isinstance(player.get("cards"), list)
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
    # Nowadays everything is capped at level 14 (15), and the different rarities
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
    Determines the result of a battle based on crown counts.

    Compares the crown count between the reference player's team and opponents
    to determine if the battle was a victory, defeat, or draw.

    Args:
        battle (dict): Battle log dictionary containing team and opponent data

    Returns:
        str: "Victory", "Defeat", or "Draw"
    """

    # Default to zero as crown amount if it can't be found in the json
    own_crowns = battle.get("team")[0].get("crowns", 0)
    opponent_crowns = battle.get("opponent")[0].get("crowns", 0)

    # Check who won
    if own_crowns > opponent_crowns:
        return "Victory"

    if own_crowns < opponent_crowns:
        return "Defeat"

    # Upon same crown amount
    return "Draw"


def clean_battle_log_list(battle_logs, player_tag):
    """
    Processes and cleans a list of battle logs from the Clash Royale API.

    Adds metadata, converts timestamps, determines game results, and removes
    unnecessary fields to prepare the data for database storage. Each battle is
    cleaned on its own: a malformed entry is logged and skipped, so it cannot
    keep the valid battles of the same log from being stored.

    Args:
        battle_logs (list): List of raw battle log dictionaries from the API
        player_tag (str): The player tag to use as reference for the battles

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
            cleaned.extend([_clean_single_battle(b, player_tag) for b in battles])
        except Exception:
            battle_time = battle.get("battleTime") if isinstance(battle, dict) else None
            logger.exception(
                "Skipped malformed battle of %s at %s", player_tag, battle_time
            )

    return cleaned


def _clean_single_battle(battle, player_tag):
    """
    Cleans one battle (or one extracted duel round) in place.

    Raises:
        ValueError: If the battle lacks a field the cleaning requires.
    """

    if not is_valid_battle(battle):
        raise ValueError("Battle is missing required fields")

    # Tag combined with time is unique identifier for each battle
    battle["referencePlayerTag"] = player_tag

    # e.g. "20250817T022935.000Z"
    battle["battleTime"] = datetime.strptime(battle["battleTime"], "%Y%m%dT%H%M%S.000Z")

    clean_battle(battle)

    battle["gameResult"] = determine_game_result(battle)
    battle["deckKey"] = build_deck_key(battle, player_tag)

    # Remove the unnecessary stats from each battle
    keys_to_remove = [
        "deckSelection",
        "isHostedMatch",
        "leagueNumber",
        "isLadderTournament",
    ]
    for key in keys_to_remove:
        battle.pop(key, None)

    # Refactor Arena and GameMode (get rid of id)
    battle["arena"] = battle["arena"].get("name")
    battle["gameMode"] = battle["gameMode"].get("name")

    return battle


def build_deck_key(battle, player_tag):
    """
    Builds the identity of the reference player's deck as one string.

    The deck statistics group battles by it, so they need not extract and sort
    every battle's cards at read time. Card levels stay out: a deck is the
    same deck at any level. Its parts are already the card filter's keys.

    NOTE The format is parsed by deck_card_filter_stages (mongo/query_utils.py)
    and has to stay identical to what the deck statistics expect:
    "<id>-<evolutionLevel>,..." sorted as strings, "|", then the tower troop
    ids sorted as strings, e.g. "26000000-1,26000010-0,...|159000000". A deck
    without a tower troop ends in "|". A card without an id uses its name.

    Args:
        battle (dict): Cleaned battle, evolution levels as stored
        player_tag (str): Tag of the player whose deck is keyed

    Returns:
        str: The deck key, "|" if the player is not on the team
    """

    # Should always use the id for the key, fallback to name if no id is present.
    # This is (hopefully) redundant and Clash Royale (hopefully) will always
    # provide a stable id for the same card.
    def identity(card):
        return card.get("id") if card.get("id") is not None else card.get("name")

    member = next((m for m in battle["team"] if m.get("tag") == player_tag), {})
    cards = sorted(
        f"{identity(card)}-{int(card.get('evolutionLevel') or 0)}"
        for card in member.get("cards") or []
    )
    towers = sorted(str(identity(tower)) for tower in member.get("supportCards") or [])
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
    for i in range(min(len(team_rounds), len(opp_rounds))):
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

        battle_time_str = current_battle.get("battleTime")
        dt = datetime.strptime(battle_time_str, "%Y%m%dT%H%M%S.000Z")

        # Shift by i seconds, so the position in the duel
        dt_shifted = dt + timedelta(seconds=i)
        current_battle["battleTime"] = dt_shifted.strftime("%Y%m%dT%H%M%S.000Z")

        # TODO (potentially) add unique match ID

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
    level 3 that no card has. Works on raw and stored battles alike, so a
    backfill can reuse it.

    NOTE Assumes evolutions never take part in a boat defense, which held for
    every battle checked so far. If the game ever lets them, this rule has to
    go, and the stored defenses need their levels back from the API.

    Args:
        battle (dict): Battle with team, opponent, gameMode (dict or name)
            and boatBattleSide, the reference player's side

    Returns:
        bool: True if the battle is a boat battle and a defense was cleaned
    """

    game_mode = battle.get("gameMode")
    if isinstance(game_mode, dict):
        game_mode = game_mode.get("name")
    if game_mode != "ClanWar_BoatBattle":
        return False

    # The team is always the reference player's side
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
        battles (list[dict]): List of battle dictionaries (newest first) as returned by the Clash Royale API.
        player_tag (str): The tag of the player whose name is being retrieved.

    Returns:
        str: The player's name if found, otherwise the given `player_tag`.
    """

    # battles[0] is the newest battle, so chances are this is the actual current name.
    # Easiest way to get the actual name would be to use the get_player_info of the clash_royale_api module,
    # but extracting it from the battle log, which is already being fetched, saves one API-call per cycle per player
    battle = battles[0]  # Take first battle of battles in the list

    # Reference player is always found in team
    for player in battle.get("team"):
        # Check if it's the reference player
        if player.get("tag") == player_tag:
            return player.get("name")

    return player_tag  # Default to the tag if the name couldn't be found
