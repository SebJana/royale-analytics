// A just-tracked player's first battle sync usually lands within seconds, but
// under heavy load or after long downtime the scraper's schedule is crowded and
// it can take minutes. Polling at a fixed short interval for that long only
// adds load while the backend is already behind.
const FIRST_SYNC_POLL_BASE_MS = 3_000; // 3 seconds
const FIRST_SYNC_POLL_MAX_MS = 60_000; // 1 minute
// 1.5 rather than 2 keeps the early polls close together, where a normal
// first sync lands: 3, 4.5, 6.75, 10, 15, 23, 34, 51, then 60 seconds.
const FIRST_SYNC_POLL_GROWTH = 1.5;

/**
 * Delay before the next poll for a just-tracked player's first battle sync,
 * growing per poll and clamped to a minute.
 *
 * @param pollCount - Polls answered so far, at least 1. React Query's
 *   `dataUpdateCount` fits, since every answer until the sync lands is a
 *   pending one.
 * @returns The delay in milliseconds
 */
export function firstSyncPollInterval(pollCount: number): number {
  const growth = FIRST_SYNC_POLL_GROWTH ** Math.max(0, pollCount - 1);
  return Math.min(FIRST_SYNC_POLL_BASE_MS * growth, FIRST_SYNC_POLL_MAX_MS);
}
