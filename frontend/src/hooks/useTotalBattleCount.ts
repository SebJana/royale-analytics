import { useQuery } from "@tanstack/react-query";
import { fetchTotalBattleCount } from "../services/api/battles";
import type { TotalBattleCount } from "../types/battles";

const min = 60_000;

/**
 * Number of battles on record, shown on the home page.
 *
 * Cached for two minutes after the home page closes.
 *
 * @returns The React Query result
 */
export function useTotalBattleCount() {
  return useQuery<TotalBattleCount, Error>({
    queryKey: ["totalBattleCount"],
    queryFn: fetchTotalBattleCount,
    // Kept briefly after home unmounts, so a quick return renders at once.
    // Later visits load fresh counts behind the loading screen.
    staleTime: 1 * min,
    gcTime: 2 * min,
  });
}
