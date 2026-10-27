import { useQuery } from "@tanstack/react-query";
import { fetchAllTrackedPlayersCount } from "../services/api/trackedPlayers";
import type { PlayerCount } from "../types/players";

const min = 60_000;

export const TRACKED_PLAYER_COUNT_QUERY_KEY = "trackedPlayerCount";

/**
 * Number of tracked players, shown on the home page.
 *
 * Cached for two minutes after the home page closes. Invalidate TRACKED_PLAYER_COUNT_QUERY_KEY
 * after adding or removing a player.
 *
 * @returns The React Query result
 */
export function useTrackedPlayerCount() {
  return useQuery<PlayerCount, Error>({
    queryKey: [TRACKED_PLAYER_COUNT_QUERY_KEY],
    queryFn: fetchAllTrackedPlayersCount,
    // Kept briefly after home unmounts, so a quick return renders at once.
    // Later visits load fresh counts behind the loading screen.
    staleTime: 1 * min,
    gcTime: 2 * min,
  });
}
