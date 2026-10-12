// Only type Badge for display of account age using the YearsPlayed Badge
type Badge = {
  name: string;
  level: number;
  maxLevel: number;
  progress: number;
  target: number;
  // There is also iconUrls, but no display needed/planned
};

export type Player = {
  tag: string;
  name: string;
  trophies: number;
  wins: number;
  losses: number;
  battleCount: number;
  threeCrownWins: number;
  clan?: {
    name: string;
  };
  arena?: {
    name: string;
  };
  badges?: Badge[];
  // When the backend last refreshed the shown data (UTC, null until the
  // first sync). Not part of the Clash Royale profile itself.
  syncInfo?: {
    battlesSyncedAt: string | null;
    profileSyncedAt: string | null;
    // Date the player was first tracked (YYYY-MM-DD)
    trackedSince: string | null;
    // Stored battles from before trackedSince, recorded through other tracked
    // players they played with or against. Only a part of what was played.
    // null when unknown (tracked before this was recorded).
    battlesBeforeTracking?: number | null;
    // Periods the player was untracked, long enough to have missed battles
    // passiveBattles: battles of the period recorded through other tracked
    // players, as battlesBeforeTracking
    trackingGaps?: {
      from: string;
      to: string;
      hours: number;
      passiveBattles?: number | null;
    }[];
  };
};
