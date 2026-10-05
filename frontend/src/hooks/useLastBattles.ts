import { useMemo, useEffect, useRef } from "react";
import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import type { LastBattles } from "../types/lastBattles";
import { validatePlayerTagSyntax } from "../utils/playerTag";
import { fetchLastBattles } from "../services/api/lastBattles";

const min = 60_000; // 1 minute in milliseconds
const cacheDuration = 5 * min;
// Poll interval while a just-tracked player's first battle sync is running.
// The backend syncs new players within seconds.
const firstSyncPollInterval = 3_000;

/**
 * Hook for fetching player battles with infinite scroll pagination
 *
 * Simple strategy:
 * - Fresh data (0-5 min): Served instantly from cache, no API calls
 * - After 5 minutes: Data is garbage collected, complete reset on next visit
 * - Continuous "Load more" functionality when data is fresh
 * - No persistence to ensure clean resets
 */
export function usePlayerBattlesInfinite(
  playerTag: string,
  limit = 50,
  enabled = true,
  beforeDate?: string,
) {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => ["playerBattles", playerTag, limit, beforeDate] as const,
    [playerTag, limit, beforeDate],
  );

  const q = useInfiniteQuery<
    LastBattles,
    Error,
    InfiniteData<LastBattles, string | undefined>,
    typeof queryKey,
    string | undefined
  >({
    queryKey,
    enabled: enabled && !!playerTag && validatePlayerTagSyntax(playerTag),
    initialPageParam: beforeDate, // Use beforeDate as initial page param if provided
    queryFn: ({ pageParam }) => fetchLastBattles(playerTag, pageParam, limit),
    getNextPageParam: (lastPage) => {
      const lb = lastPage.last_battles;
      // Stop pagination if no battles are returned
      if (!lb?.battles?.length) return undefined;
      // Use earliest battle time for next page's "before" parameter (pagination goes backwards in time)
      return lb.earliestBattleTime ?? undefined;
    },
    // Data is considered "fresh" (no refetching during this period)
    staleTime: cacheDuration,
    // Keep data in memory - after that it gets garbage collected
    gcTime: cacheDuration,
    // Poll only until the first battle sync of a just-tracked player finished
    refetchInterval: (query) =>
      query.state.data?.pages[0]?.first_sync_pending
        ? firstSyncPollInterval
        : false,
    // Disable automatic refetching to reduce unnecessary API calls
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    // Retry failed requests up to 2 times with exponential backoff
    retry: 2,
    retryDelay: (i) => Math.min(1000 * 2 ** i, 30_000),

    // Disable persistence to prevent restoring many pages at once
    // When user hits reload they're back to seeing only the last X default loaded battles
    meta: { persist: false },
  });

  // The battles page polls faster than the profile while a just-tracked
  // player's first sync runs. Once the battles arrive, refresh the profile
  // too, so its "Battles updated" hint matches the battles shown.
  const firstSyncPending = q.data?.pages[0]?.first_sync_pending === true;
  const wasFirstSyncPending = useRef(firstSyncPending);
  useEffect(() => {
    if (wasFirstSyncPending.current && !firstSyncPending) {
      queryClient.invalidateQueries({ queryKey: ["playerProfile", playerTag] });
    }
    wasFirstSyncPending.current = firstSyncPending;
  }, [firstSyncPending, playerTag, queryClient]);

  // Force reset to first page when data becomes stale to prevent mass API calls
  useEffect(() => {
    if (q.isStale && q.data?.pages && q.data.pages.length > 1) {
      queryClient.resetQueries({ queryKey });
    }
  }, [q.isStale, q.data, queryClient, queryKey]);

  return q;
}
