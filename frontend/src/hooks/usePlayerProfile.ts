import { useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import axios from "axios";
import { fetchPlayerProfile } from "../services/api/player";
import type { Player } from "../types/player";
import { PREFERENCE_QUERY_META } from "../utils/storage";
import { firstSyncPollInterval } from "../utils/polling";

const min = 60_000;
// Poll interval while a player has no profile snapshot yet: players inserted
// without the API, e.g. from the top players' leaderboard, until the
// scraper's first refresh.
const notSyncedPollInterval = 30_000;
// Poll interval of a synced profile on an open page, the same as the sync
// version poll (useSyncVersionRefresh.ts). The route only reads the database.
const profileRefreshInterval = 2 * min;

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

/**
 * Whether a PROFILE_NOT_SYNCED answer belongs to a parked player, which gets
 * no profile until it is tracked again.
 *
 * @param error - Error of the profile query
 * @returns True for a parked player without a stored profile
 */
function isParkedWithoutProfile(error: unknown): boolean {
  if (
    !axios.isAxiosError<{ detail?: { tracking?: { state?: string } } }>(error)
  )
    return false;
  const detail = error.response?.data?.detail;
  return typeof detail === "object" && detail?.tracking?.state === "parked";
}

// Answers that a retry cannot change
const PERMANENT_ERROR_CODES = new Set([
  "INVALID_PLAYER_TAG",
  "PLAYER_NOT_TRACKED",
  "PROFILE_NOT_SYNCED",
]);

/**
 * The stored profile of a player, polled while the page is open.
 *
 * A query that never had data goes back to pending on every refetch, which
 * clears its error. For a player without a profile (PROFILE_NOT_SYNCED) every
 * poll would swap the whole page for the loading spinner and remount it. So
 * while such a refetch runs, the last error of this player is reported as if
 * it were still settled.
 *
 * @param playerTag - The player's tag as in the page URL
 * @returns The React Query result, settled on the last error during refetches
 */
export function usePlayerProfile(
  playerTag: string,
): UseQueryResult<Player, Error> {
  const query = useProfileQuery(playerTag);
  const [lastError, setLastError] = useState<{
    tag: string;
    error: Error;
  } | null>(null);
  // Adjusted while rendering, like derived state
  if (
    query.error &&
    (lastError?.tag !== playerTag || lastError.error !== query.error)
  )
    setLastError({ tag: playerTag, error: query.error });
  const refetchingError =
    query.isPending && query.isFetching && lastError?.tag === playerTag
      ? lastError.error
      : null;
  if (!refetchingError) return query;
  return {
    ...query,
    status: "error",
    error: refetchingError,
    isError: true,
    isPending: false,
    isLoading: false,
    isLoadingError: true,
  } as UseQueryResult<Player, Error>;
}

function useProfileQuery(playerTag: string) {
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
      const data = query.state.data;
      const syncInfo = data?.syncInfo;
      // Until the first battle sync lands, so the "Battles updated" hint does
      // not stay at "not synced yet". Backs off while it takes long. A
      // parked player is not synced, so it is not waited for.
      if (
        syncInfo &&
        !syncInfo.battlesSyncedAt &&
        data?.tracking?.state !== "parked"
      )
        return firstSyncPollInterval(query.state.dataUpdateCount);
      // A parked player gets no profile until it is tracked again, maybe
      // from another tab or by another visitor. The slower poll notices that.
      if (getProfileErrorCode(query.state.error) === "PROFILE_NOT_SYNCED")
        return isParkedWithoutProfile(query.state.error)
          ? profileRefreshInterval
          : notSyncedPollInterval;
      // Profile snapshots and battle checks without new battles leave the
      // syncVersion as it is, so an open page refreshes the name, clan and
      // "Battles updated" hint on its own
      if (query.state.data) return profileRefreshInterval;
      return false;
    },
    retry: (failureCount, error) =>
      failureCount < 3 &&
      !PERMANENT_ERROR_CODES.has(getProfileErrorCode(error) ?? ""),
    refetchOnWindowFocus: false,
    meta: PREFERENCE_QUERY_META,
  });
}
