import { useCallback } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAllCards } from "../services/api/cards";
import type { CardMeta } from "../types/cards";
import { NECESSARY_QUERY_META } from "../utils/storage";

const min = 60_000;

// TODO pull cache times into settings file
// Shared with the card image preload, so both read the same cache entry.
export const cardsQueryOptions = queryOptions<CardMeta[], Error>({
  queryKey: ["cards"],
  queryFn: fetchAllCards,
  staleTime: 15 * min, // Cache duration, how long cards are considered fresh and aren't re-fetched from the backend
  gcTime: 30 * min,
  // An open page picks up a newer list (new cards, a new image set) without a
  // reload. One cached request for the whole tab, and only while visible.
  refetchInterval: 15 * min,
  refetchOnWindowFocus: false,
  meta: NECESSARY_QUERY_META,
});

// One refetch per minute at most, however many cards on screen are unknown or
// fail to load. The server learns new cards only on the data scraper's next
// refresh, so retrying faster gains nothing.
const CARDS_REFRESH_COOLDOWN = 1 * min;
let lastCardsRefresh = 0;
let pendingRefresh: number | undefined;

export function useCards() {
  return useQuery(cardsQueryOptions);
}

/**
 * Returns a callback that refetches the card list, rate limited across the
 * whole tab.
 *
 * A tab can hold its card list for hours, so battle data may name a card
 * released since then, or its self-hosted image URLs may point to a set the
 * server has removed. Both show up while rendering, which is when this is
 * called.
 *
 * Every call goes through one pending timer, immediate once the cooldown is
 * over. A call inside the cooldown is therefore deferred instead of dropped,
 * and calls while a refetch is pending share it, so two can never overlap.
 *
 * @returns A stable callback that is safe to call on every render or error.
 */
export function useRefreshCards() {
  const queryClient = useQueryClient();

  return useCallback(() => {
    if (pendingRefresh !== undefined) return;
    const wait = lastCardsRefresh + CARDS_REFRESH_COOLDOWN - Date.now();
    pendingRefresh = window.setTimeout(
      () => {
        pendingRefresh = undefined;
        lastCardsRefresh = Date.now();
        void queryClient.invalidateQueries({
          queryKey: cardsQueryOptions.queryKey,
        });
      },
      Math.max(0, wait),
    );
  }, [queryClient]);
}
