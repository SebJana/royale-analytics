import type { Card } from "./cards";

export type CardStats = {
  player_tag: string;
  game_modes: string[] | null; // Applied filters on game mode
  card_statistics: {
    totalBattles: number;
    cards: Cards[];
    // Tower troops, id 0 (NO_SUPPORT_ID) for battles without tower data
    supportCards: Cards[];
  };
};

type Cards = {
  usage: number;
  wins: number;
  card: Card;
  winRate: number;
};
