export type DailyStats = {
  player_tag: string;
  game_modes: string[] | null; // Applied filters on game mode
  exclude_game_modes: string[]; // Applied game modes left out instead
  daily_statistics: {
    totalBattles: number;
    daily: Days[];
  };
  // The player's sync version the response was computed at (see
  // useSyncVersionRefresh)
  syncVersion?: number;
};

export type Days = {
  date: string;
  battles: number;
  victories: number;
  defeats: number;
  draws: number;
  crownsFor: number;
  crownsAgainst: number;
  elixirLeaked: number; // Summed leaked elixir
  winRate: number;
};
