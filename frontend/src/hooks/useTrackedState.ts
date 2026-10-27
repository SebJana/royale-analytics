import { useQuery } from "@tanstack/react-query";
import { searchTrackedPlayers } from "../services/api/trackedPlayers";
import type { PlayerSearchResponse, TrackedState } from "../types/players";
import { normalizePlayerTag } from "../utils/playerTag";

const min = 60_000;

export const TRACKED_STATE_QUERY_KEY = "trackedState";

/**
 * Whether a player tag is tracked, looked up once `enabled` turns true.
 *
 * Asks the player search for the full tag: an existing tag is pinned first
 * with the match "exactTag", one dict lookup in the API. Keyed by the
 * normalized tag, so "#abc" and "ABC" share one cache entry, and kept for the
 * session: hovering the same opponent again, in this battle or another one,
 * asks the API only after 15 minutes. Invalidate TRACKED_STATE_QUERY_KEY after
 * adding or removing a player.
 *
 * @param playerTag - The tag as shown, e.g. "#YYRJQY28"
 * @param enabled - Look up now; false until the user shows interest
 * @returns The React Query result; disabled for a tag that cannot exist
 */
export function useTrackedState(playerTag: string, enabled: boolean) {
  const tag = normalizePlayerTag(playerTag);
  return useQuery<PlayerSearchResponse, Error, TrackedState>({
    queryKey: [TRACKED_STATE_QUERY_KEY, tag],
    queryFn: ({ signal }) => searchTrackedPlayers(tag!, signal),
    select: (response) => {
      const first = response.players[0];
      return first?.match === "exactTag"
        ? { tag: first.tag, tracked: true }
        : { tag: tag!, tracked: false };
    },
    enabled: enabled && tag !== null,
    staleTime: 15 * min,
    gcTime: 60 * min,
    // A failed lookup (429, or 503 while the API builds its index) only means
    // no link; the component asks again on the next hover.
    retry: false,
    // No storage meta, so never persisted (main.tsx): tracked players change,
    // and a fresh session asks again at the cost of one request per hover.
  });
}
