// "parked": taken off syncing to make room for other players. Its data stops
// at that time, and tracking it again resumes the syncs. Also sent with a
// PROFILE_NOT_SYNCED error, for a player parked before its first profile.
export type PlayerTracking = {
  state: "active" | "parked";
  // Day the pause started (YYYY-MM-DD), null if unknown
  since?: string | null;
  // Battles of the pause recorded through other tracked players, as
  // passiveBattles of a tracking gap
  passiveBattles?: number | null;
};

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
  // Only set for the most viewed players (top of the explore list). The
  // count is rounded by the backend and covers the last 30 days.
  popularity?: {
    rank: number;
    views30d: number;
  } | null;
  tracking?: PlayerTracking;
  // When the backend last refreshed the shown data (UTC, null until the
  // first sync). Not part of the Clash Royale profile itself.
  syncInfo?: {
    battlesSyncedAt: string | null;
    // Rises with every sync that stores battles of the player
    // (see useSyncVersionRefresh)
    syncVersion?: number;
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
