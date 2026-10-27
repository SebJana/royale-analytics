import type { Card } from "./cards";

export type Player = {
  tag: string;
  name: string;
  crowns: number;
  kingTowerHitPoints: number;
  princessTowersHitPoints: number[];
  cards: Card[];
  supportCards?: Card[];
  elixirLeaked: number;
};

export type Battle = {
  battleTime: string;
  arena: string;
  gameMode: string;
  team: Player[];
  opponent: Player[];
  gameResult: "Victory" | "Defeat" | "Draw";
};

export type LastBattles = {
  player_tag: string;
  last_battles: {
    battles: Battle[];
    latestBattleTime: string;
    earliestBattleTime: string;
  };
  // Set while a just-tracked player's first battle sync has not finished,
  // also when battles stored by other players' syncs are already shown
  first_sync_pending?: boolean;
  // Whether the page starts with the player's newest stored battle, so a
  // streak running into it is still going (as of the last sync).
  includes_newest_battle?: boolean;
  // The player's sync version the response was computed at (see
  // useSyncVersionRefresh)
  syncVersion?: number;
};
