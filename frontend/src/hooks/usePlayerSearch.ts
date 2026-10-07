import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { searchTrackedPlayers } from "../services/api/trackedPlayers";
import type { PlayerSearchResponse } from "../types/players";

const min = 60_000;
// Searching waits this long after the last keystroke, so fast typing sends
// one request instead of one per character.
const debounceMs = 150;
// NOTE: Match SEARCH_QUERY_MAX_LENGTH in the API settings, which answers
// longer queries with 422.
const maxQueryLength = 15;

export const PLAYER_SEARCH_QUERY_KEY = "playerSearch";

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/**
 * Search tracked players while the user types.
 *
 * Queries are debounced, an outdated request is aborted when a newer one
 * starts, and the previous results stay visible until the new ones arrive.
 * Invalidate PLAYER_SEARCH_QUERY_KEY after adding or removing a player.
 *
 * @param query - The search input as typed
 * @returns The React Query result; disabled while the input is empty
 */
export function usePlayerSearch(query: string) {
  const debounced = useDebouncedValue(
    query.trim().slice(0, maxQueryLength),
    debounceMs,
  );
  return useQuery<PlayerSearchResponse, Error>({
    queryKey: [PLAYER_SEARCH_QUERY_KEY, debounced],
    queryFn: ({ signal }) => searchTrackedPlayers(debounced, signal),
    enabled: debounced.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    gcTime: 5 * min,
    // No storage meta, so never persisted (main.tsx): restoring one-off
    // results after a reload would only show stale names.
  });
}
