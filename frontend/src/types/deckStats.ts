import type { Card } from "./cards";

export type DeckStats = {
  player_tag: string;
  game_modes: string[] | null; // Applied filters on game mode
  exclude_game_modes: string[]; // Applied game modes left out instead
  deck_statistics: {
    // Every battle of the time range and game modes, before the card filter
    totalBattles: number;
    // Totals over every deck the filters keep, also those not returned
    deckCount: number;
    battleCount: number;
    wins: number;
    // The top decks of the sort order, at most the backend's
    // DECK_STATS_LIMIT (250). Fewer than deckCount means decks were left out.
    decks: Deck[];
  };
};

// Deck order the backend sorts by before it caps the list. Usage rate orders
// like battleCount.
export type DeckSortBy = "battleCount" | "wins" | "winRate" | "lastSeen";

export type DeckSort = {
  sortBy: DeckSortBy;
  sortOrder: "asc" | "desc";
  // Decks played fewer times are left out, from the list and the totals
  minBattles: number;
};

export type Deck = {
  battleCount: number; // Battles played with this deck
  wins: number;
  firstSeen: string;
  lastSeen: string;
  modes: string[]; // Game modes in which the deck appeared
  deck: Card[];
  support: Card[]; // Tower troop, empty if the battles have no tower data
  winRate: number;
  // Selected cards in the deck, plus 1 for a selected tower troop. Only in
  // match mode, where the decks come ranked by it.
  matchedCardCount?: number;
};

// Card filter of the deck statistics, applied by the backend. Cards are keys
// from getCardFilterKey, tower troops ids.
export type DeckCardFilter = {
  mode: "include" | "match";
  cards: string[];
  excludeCards: string[];
  supportIds: number[];
  excludeSupportIds: number[];
};
