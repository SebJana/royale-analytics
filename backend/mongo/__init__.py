from .connection import MongoConn
from .battles_read import (
    get_battles_count,
    print_first_battles,
    get_last_battles,
    get_decks_win_percentage,
    get_cards_win_percentage,
    get_daily_stats,
)
from .battles_write import insert_battles

from .players_read import (
    get_tracked_player_tags,
    get_tracked_players,
    get_tracked_players_page,
    get_players_changed_since,
    check_player_tracked,
    get_players_count,
    get_tracked_player_cache_state,
    get_player_sync_state,
    get_tracked_players_sync_times,
)
from .players_write import (
    insert_tracked_player,
    deactivate_tracked_player,
    record_battle_sync,
    record_battle_sync_failure,
    ensure_search_change_index,
)

from .game_modes_write import insert_game_modes
from .game_modes_read import get_game_modes

from .cards_read import get_cards
from .cards_write import save_cards

from .player_profiles_read import get_player_profile
from .player_profiles_write import save_player_profile

from .database_read import get_database_health, get_server_time

__all__ = [
    "MongoConn",
    # battles
    ## read
    "get_battles_count",
    "print_first_battles",
    "get_last_battles",
    "get_decks_win_percentage",
    "get_cards_win_percentage",
    "get_daily_stats",
    ## write
    "insert_battles",
    # players
    ## read
    "get_tracked_player_tags",
    "get_tracked_players",
    "get_tracked_players_page",
    "get_players_changed_since",
    "check_player_tracked",
    "get_players_count",
    "get_tracked_player_cache_state",
    "get_player_sync_state",
    "get_tracked_players_sync_times",
    ## write
    "insert_tracked_player",
    "deactivate_tracked_player",
    "record_battle_sync",
    "record_battle_sync_failure",
    "ensure_search_change_index",
    # game_modes
    ## read
    "get_game_modes",
    ## write
    "insert_game_modes",
    # cards
    ## read
    "get_cards",
    ## write
    "save_cards",
    # player_profiles
    ## read
    "get_player_profile",
    ## write
    "save_player_profile",
    # database
    ## read
    "get_database_health",
    "get_server_time",
]
