import type { Card } from "./cards";

export type CardStats = {
  player_tag: string;
  game_modes: string[] | null; // Applied filters on game mode
  exclude_game_modes: string[]; // Applied game modes left out instead
  card_statistics: {
    totalBattles: number;
    cards: Cards[];
    // Tower troops, id 0 (NO_SUPPORT_ID) for battles without tower data
    supportCards: Cards[];
  };
  // The player's sync version the response was computed at (see
  // useSyncVersionRefresh)
  syncVersion?: number;
};

type Cards = {
  usage: number;
  wins: number;
  card: Card;
  winRate: number;
};
