import { useQuery } from "@tanstack/react-query";
import { fetchCardStats } from "../services/api/cardStats";
import type { CardStats } from "../types/cardStats";
import type { TimeRange } from "../types/seasons";
import type { GameModeQuery } from "../types/gameModes";
import { PREFERENCE_QUERY_META } from "../utils/storage";

const min = 60_000;

export function useCardStats(
  playerTag: string,
  range: TimeRange,
  gameModes?: GameModeQuery | null, // Can be null to disable query until game modes are initialized
) {
  return useQuery<CardStats, Error>({
    // Plain objects, which React Query hashes by value
    queryKey: ["cardStats", playerTag, range, gameModes ?? undefined],
    // Pass the playerTag to the query function from the query key
    queryFn: ({ queryKey }) => {
      const [, tag, timeRange, modes] = queryKey as [
        string,
        string,
        TimeRange,
        GameModeQuery | undefined,
      ];
      return fetchCardStats(tag, timeRange, modes);
    },
    staleTime: 10 * min, // Cache duration, how long cards are considered fresh and aren't re-fetched from the backend
    gcTime: 15 * min,
    refetchOnWindowFocus: false,
    retry: false, // Don't retry to avoid long waits when no data is found
    enabled: gameModes !== null, // Only run query when gameModes are initialized (prevents double loading)
    meta: PREFERENCE_QUERY_META,
  });
}
