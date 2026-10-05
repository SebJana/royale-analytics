import { useQuery } from "@tanstack/react-query";
import { fetchDeckStats } from "../services/api/deckStats";
import type { DeckCardFilter, DeckSort, DeckStats } from "../types/deckStats";

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
  startDate: string,
  endDate: string,
  gameModes?: string[] | null, // Can be null to disable query until game modes are initialized
  cardFilter?: DeckCardFilter,
  sort?: DeckSort,
  enabled = true,
) {
  const modesKey = (gameModes ?? []).join("|"); // Make game modes a stable key
  // Plain objects, which React Query hashes by value
  const filterKey = [
    "deckStats",
    playerTag,
    startDate,
    endDate,
    modesKey,
    cardFilter,
  ];

  return useQuery<DeckStats, Error>({
    queryKey: [...filterKey, sort],
    // Pass the playerTag to the query function from the query key
    queryFn: ({ queryKey }) => {
      const [, tag, start, end, modesString, filter, order] = queryKey as [
        string,
        string,
        string,
        string,
        string,
        DeckCardFilter | undefined,
        DeckSort | undefined,
      ];
      const modes = modesString ? modesString.split("|") : undefined; // back to array from joined string
      return fetchDeckStats(tag, start, end, modes, filter, order);
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
  });
}
