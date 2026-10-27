// A Clash Royale season as /seasons returns it
export type Season = {
  id: string; // "YYYY-MM", e.g. "2026-09"
  start: string; // UTC ISO 8601, inclusive
  end: string; // UTC ISO 8601, exclusive
  isCurrent: boolean;
};

// Timespan of a statistics request. With a season the backend resolves its
// exact window and the dates only serve display.
export type TimeRange = {
  startDate: string; // YYYY-MM-DD in the browser's timezone
  endDate: string; // YYYY-MM-DD, inclusive
  season: string | null;
};
