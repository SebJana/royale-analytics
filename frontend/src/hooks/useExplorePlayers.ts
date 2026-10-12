import { useInfiniteQuery } from "@tanstack/react-query";
import { fetchExplorePlayers } from "../services/api/trackedPlayers";
import type { ExplorePage } from "../types/players";

const min = 60_000;
// The first page is a short teaser under the search, later pages are longer.
const FIRST_PAGE_SIZE = 5;
const NEXT_PAGE_SIZE = 10;

/**
 * Page through the most viewed tracked players of the last 30 days.
 *
 * The backend ranks players every few hours, so the list is cached for a
 * while. Not persisted (no storage meta, see main.tsx): it is the same for
 * every visitor and cheap to fetch again.
 *
 * @param enabled - Fetch only once the list is shown
 * @returns The infinite query; each page holds players and the next cursor
 */
export function useExplorePlayers(enabled = true) {
  return useInfiniteQuery<ExplorePage, Error>({
    queryKey: ["explorePlayers"],
    queryFn: ({ pageParam, signal }) => {
      const cursor = pageParam as number;
      return fetchExplorePlayers(
        cursor,
        cursor === 0 ? FIRST_PAGE_SIZE : NEXT_PAGE_SIZE,
        signal,
      );
    },
    initialPageParam: 0,
    enabled,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    staleTime: 10 * min,
    gcTime: 30 * min,
    retry: 1,
    refetchOnWindowFocus: false,
  });
}
