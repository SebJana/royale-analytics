from .connection import MongoConn, is_mongo_timeout, request_deadline
from .battles_read import (
    get_battles_count,
    print_first_battles,
    get_last_battles,
    get_decks_win_percentage,
    get_cards_win_percentage,
    get_daily_stats,
    split_battle_copies,
    count_battles_in_ranges,
)
from .battles_write import insert_battles

from .players_read import (
    PARKED_REASON,
    NOT_PROTECTED,
    get_tracked_player_tags,
    get_tracked_players,
    get_tracked_players_page,
    get_players_changed_since,
    check_player_tracked,
    check_player_protected,
    get_players_count,
    get_tracked_player_cache_state,
    get_player_sync_state,
    get_tracked_players_sync_times,
    get_explore_page,
    get_top_players_awaiting_sync,
    get_former_top_players,
    get_parkable_players_count,
    get_park_candidates,
)
from .players_write import (
    insert_tracked_player,
    deactivate_tracked_player,
    remove_parked_player,
    record_battle_sync,
    record_battle_sync_failure,
    bump_sync_versions,
    ensure_search_change_index,
    write_view_ranking,
    migrate_regular_players,
    set_player_regular,
    write_top_finish,
    retire_former_top_player,
)

from .player_views_read import (
    sum_player_views,
    get_player_view_baselines,
    get_player_views_on,
)
from .player_views_write import ensure_player_views_indexes, record_profile_view

from .top_seasons_read import get_top_season, get_top_season_page
from .top_seasons_write import save_top_season, mark_top_season_applied

from .game_modes_write import insert_game_modes
from .game_modes_read import get_game_modes

from .cards_read import get_cards
from .cards_write import save_cards

from .player_profiles_read import get_player_profile
from .player_profiles_write import save_player_profile

from .database_read import get_database_health, get_server_time

__all__ = [
    "MongoConn",
    "is_mongo_timeout",
    "request_deadline",
    # battles
    ## read
    "get_battles_count",
    "print_first_battles",
    "get_last_battles",
    "get_decks_win_percentage",
    "get_cards_win_percentage",
    "get_daily_stats",
    "split_battle_copies",
    "count_battles_in_ranges",
    ## write
    "insert_battles",
    # players
    "PARKED_REASON",
    "NOT_PROTECTED",
    ## read
    "get_tracked_player_tags",
    "get_tracked_players",
    "get_tracked_players_page",
    "get_players_changed_since",
    "check_player_tracked",
    "check_player_protected",
    "get_players_count",
    "get_tracked_player_cache_state",
    "get_player_sync_state",
    "get_tracked_players_sync_times",
    "get_explore_page",
    "get_top_players_awaiting_sync",
    "get_former_top_players",
    "get_parkable_players_count",
    "get_park_candidates",
    ## write
    "insert_tracked_player",
    "deactivate_tracked_player",
    "remove_parked_player",
    "record_battle_sync",
    "record_battle_sync_failure",
    "bump_sync_versions",
    "ensure_search_change_index",
    "write_view_ranking",
    "migrate_regular_players",
    "set_player_regular",
    "write_top_finish",
    "retire_former_top_player",
    # player_views
    ## read
    "sum_player_views",
    "get_player_view_baselines",
    "get_player_views_on",
    ## write
    "ensure_player_views_indexes",
    "record_profile_view",
    # top_seasons
    ## read
    "get_top_season",
    "get_top_season_page",
    ## write
    "save_top_season",
    "mark_top_season_applied",
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
