import { useInfiniteQuery, type InfiniteData } from "@tanstack/react-query";
import { fetchTopPlayers } from "../services/api/trackedPlayers";
import type { TopPage } from "../types/players";

const min = 60_000;
// The first page is a short teaser under the search, later pages are longer.
const FIRST_PAGE_SIZE = 5;
const NEXT_PAGE_SIZE = 10;

type TopPageParam = { cursor: number; season: string | null };

/**
 * Page through the last finished season's top players, best rank first.
 *
 * Later pages ask for the first page's season, so a list paged across a
 * rollover stays one season; a refetch starts again from the newest one.
 *
 * The list changes once a season, so it is cached for a long while. Not
 * persisted (no storage meta, see main.tsx): it is the same for every
 * visitor and cheap to fetch again.
 *
 * @param enabled - Fetch only once the list is shown
 * @returns The infinite query; each page holds players, their season and
 *   the next cursor
 */
export function useTopPlayers(enabled = true) {
  return useInfiniteQuery<
    TopPage,
    Error,
    InfiniteData<TopPage, TopPageParam>,
    string[],
    TopPageParam
  >({
    queryKey: ["topPlayers"],
    queryFn: ({ pageParam, signal }) =>
      fetchTopPlayers(
        pageParam.cursor,
        pageParam.cursor === 0 ? FIRST_PAGE_SIZE : NEXT_PAGE_SIZE,
        pageParam.season,
        signal,
      ),
    initialPageParam: { cursor: 0, season: null },
    enabled,
    getNextPageParam: (lastPage, pages) =>
      lastPage.nextCursor === null
        ? undefined
        : { cursor: lastPage.nextCursor, season: pages[0].season },
    staleTime: 60 * min,
    gcTime: 120 * min,
    retry: 1,
    refetchOnWindowFocus: false,
  });
}
