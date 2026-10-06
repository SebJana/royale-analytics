import { useQuery } from "@tanstack/react-query";
import { fetchSeasons } from "../services/api/seasons";
import type { Season } from "../types/seasons";

const min = 60_000;

// setTimeout fires at once beyond about 24.8 days, shorter than a season, so
// a longer wait is split into daily refetches
const MAX_REFETCH_DELAY = 24 * 60 * min;
// The backend switches seasons at the exact end, a short delay keeps clock
// differences from fetching the old list again
const AFTER_END_DELAY = 30_000;

/**
 * React Query hook for the seasons the timespan filter offers.
 * Staleness alone schedules no request, and window focus refetching is off,
 * so the list is refetched when the current season ends. An open tab then
 * marks the new season current within a minute of the reset.
 */
export function useSeasons() {
  return useQuery<Season[], Error>({
    queryKey: ["seasons"],
    queryFn: fetchSeasons,
    staleTime: 60 * min,
    gcTime: 120 * min,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const current = query.state.data?.find((season) => season.isCurrent);
      if (!current) return false;
      const untilEnd = new Date(current.end).getTime() - Date.now();
      return Math.min(
        Math.max(untilEnd, 0) + AFTER_END_DELAY,
        MAX_REFETCH_DELAY,
      );
    },
    // A tab in the background through the reset updates too
    refetchIntervalInBackground: true,
  });
}
