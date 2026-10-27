import { useQuery } from "@tanstack/react-query";
import { fetchDeckStats } from "../services/api/deckStats";
import type { DeckCardFilter, DeckSort, DeckStats } from "../types/deckStats";
import type { TimeRange } from "../types/seasons";
import type { GameModeQuery } from "../types/gameModes";
import { PREFERENCE_QUERY_META } from "../utils/storage";

const min = 60_000;

/**
 * React Query hook for the deck statistics, see fetchDeckStats.
 * A different card filter or sort is a different query, so each one is cached
 * apart. While only the sort changes, the previous decks stay on screen until
 * the new order arrives, instead of the loading spinner.
 *
 * @returns The query result; idle while gameModes is null
 */
export function useDeckStats(
  playerTag: string,
  range: TimeRange,
  gameModes?: GameModeQuery | null, // Can be null to disable query until game modes are initialized
  cardFilter?: DeckCardFilter,
  sort?: DeckSort,
  enabled = true,
) {
  // Plain objects, which React Query hashes by value
  const filterKey = [
    "deckStats",
    playerTag,
    range,
    gameModes ?? undefined,
    cardFilter,
  ];

  return useQuery<DeckStats, Error>({
    queryKey: [...filterKey, sort],
    // Pass the playerTag to the query function from the query key
    queryFn: ({ queryKey }) => {
      const [, tag, timeRange, modes, filter, order] = queryKey as [
        string,
        string,
        TimeRange,
        GameModeQuery | undefined,
        DeckCardFilter | undefined,
        DeckSort | undefined,
      ];
      return fetchDeckStats(tag, timeRange, modes, filter, order);
    },
    // Same filters, other sort: keep showing the previous decks
    placeholderData: (previous, previousQuery) =>
      previousQuery &&
      JSON.stringify(previousQuery.queryKey.slice(0, -1)) ===
        JSON.stringify(filterKey)
        ? previous
        : undefined,
    staleTime: 10 * min, // Cache duration, how long cards are considered fresh and aren't re-fetched from the backend
    gcTime: 15 * min,
    refetchOnWindowFocus: false,
    retry: false, // Don't retry to avoid long waits when no data is found
    // Only run query when gameModes are initialized (prevents double loading)
    enabled: gameModes !== null && enabled,
    meta: PREFERENCE_QUERY_META,
  });
}
