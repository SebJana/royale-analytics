import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { fetchPlayerProfile } from "../services/api/player";
import type { Player } from "../types/player";

const min = 60_000;
// Poll interval while a just-tracked player's first battle sync is running,
// so the "Battles updated" hint does not stay at "not synced yet".
const firstSyncPollInterval = 3_000;
// Poll interval while a player has no profile snapshot yet. Only players
// inserted without the API (e.g. in bulk), until the scraper's first refresh.
const notSyncedPollInterval = 30_000;

/**
 * Read the backend error code of a failed profile request, if any.
 *
 * @param error - Error of the profile query
 * @returns The detail code (e.g. "PROFILE_NOT_SYNCED") or undefined
 */
export function getProfileErrorCode(error: unknown): string | undefined {
  if (!axios.isAxiosError<{ detail?: { code?: string } }>(error)) return;
  const detail = error.response?.data?.detail;
  return typeof detail === "object" ? detail?.code : undefined;
}

// Answers that a retry cannot change
const PERMANENT_ERROR_CODES = new Set([
  "INVALID_PLAYER_TAG",
  "PLAYER_NOT_TRACKED",
  "PROFILE_NOT_SYNCED",
]);

export function usePlayerProfile(playerTag: string) {
  return useQuery<Player, Error>({
    queryKey: ["playerProfile", playerTag],
    // Pass the playerTag to the query function from the query key
    queryFn: ({ queryKey }) => {
      const [, tag] = queryKey;
      return fetchPlayerProfile(tag as string);
    },
    // Short, so the "updated ... ago" hint follows the data scraper's battle
    // syncs. The profile route only reads the database, so refetches are cheap.
    staleTime: 1 * min,
    gcTime: 30 * min,
    refetchInterval: (query) => {
      const syncInfo = query.state.data?.syncInfo;
      if (syncInfo && !syncInfo.battlesSyncedAt) return firstSyncPollInterval;
      if (getProfileErrorCode(query.state.error) === "PROFILE_NOT_SYNCED")
        return notSyncedPollInterval;
      return false;
    },
    retry: (failureCount, error) =>
      failureCount < 3 &&
      !PERMANENT_ERROR_CODES.has(getProfileErrorCode(error) ?? ""),
    refetchOnWindowFocus: false,
  });
}
