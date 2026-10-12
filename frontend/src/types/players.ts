export type PlayerCount = {
  activePlayerCount: number;
};

// NOTE: Keep in sync with MATCH_TYPES in backend/app/src/player_search/index.py.
export type PlayerSearchMatch =
  | "exactTag"
  | "exactName"
  | "namePrefix"
  | "nameWord"
  | "nameInfix"
  | "tagPrefix"
  | "tagInfix";

export type PlayerSearchResult = {
  tag: string;
  name: string;
  match: PlayerSearchMatch;
};

export type TrackedState = {
  // The tracked player's tag as stored, else the tag that was looked up
  tag: string;
  tracked: boolean;
};

export type PlayerSearchResponse = {
  // Best match first
  players: PlayerSearchResult[];
  // More players match than were returned
  hasMore: boolean;
};

export type PopularPlayer = {
  tag: string;
  name: string | null;
  // Place in the explore list, 1 is the most viewed
  rank: number;
  // Visitors in the last 30 days, rounded by the backend
  views30d: number;
};

export type ExplorePage = {
  // Most viewed first
  players: PopularPlayer[];
  // Rank to continue after, null on the last page
  nextCursor: number | null;
};
