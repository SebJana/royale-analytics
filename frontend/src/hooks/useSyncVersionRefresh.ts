import { useEffect, useRef } from "react";
import {
  useQuery,
  useQueryClient,
  type Query,
  type QueryClient,
} from "@tanstack/react-query";
import { fetchSyncVersion } from "../services/api/syncVersion";
import { validatePlayerTagSyntax } from "../utils/playerTag";

// How often an open player page asks for the sync version. React Query
// pauses the interval while the tab is hidden.
const POLL_INTERVAL = 2 * 60_000; // 2 minutes

// NOTE The cached player queries whose responses carry the syncVersion they
// were computed at, all keyed [name, playerTag, ...]. A new one belongs here
// once its backend route adds syncVersion.
const VERSIONED_QUERIES = new Set([
  "playerProfile",
  "playerBattles",
  "cardStats",
  "deckStats",
  "dailyStats",
]);

type Versioned = { syncVersion?: number; syncInfo?: { syncVersion?: number } };

/**
 * The syncVersion a cached query's data was computed at: the profile's
 * syncInfo, the first page of a paged query, or the response itself.
 * Undefined without data or without the field.
 */
function dataVersion(query: Query): number | undefined {
  const data = query.state.data as
    Versioned | { pages?: Versioned[] } | undefined;
  if (!data) return undefined;
  if ("pages" in data) return data.pages?.[0]?.syncVersion;
  const versioned = data as Versioned;
  return versioned.syncInfo?.syncVersion ?? versioned.syncVersion;
}

/**
 * Invalidates one query if its data is older than `latest`, at most once per
 * version: a refetch that still answers with the old version (a request that
 * read it just before the sync) cannot start a loop. A refetch that fails
 * clears that mark again (the cache listener below), so the next poll retries
 * it.
 *
 * Battles with older pages loaded on an open page are left alone: the
 * refetch would cost a request per page, and the reset that avoids it
 * (useLastBattles.ts) would take the reader back to the newest battles. They
 * catch up once the battles page closes.
 */
function reconcile(
  queryClient: QueryClient,
  query: Query,
  playerTag: string,
  latest: number,
  invalidatedFor: Map<string, number>,
) {
  const [name, tag] = query.queryKey;
  if (tag !== playerTag || !VERSIONED_QUERIES.has(String(name))) return;
  const version = dataVersion(query);
  if (version === undefined || version >= latest) return;
  if ((invalidatedFor.get(query.queryHash) ?? -1) >= latest) return;
  const pages = (query.state.data as { pages?: unknown[] }).pages;
  if (pages && pages.length > 1 && query.getObserversCount() > 0) return;
  invalidatedFor.set(query.queryHash, latest);
  queryClient.invalidateQueries({ queryKey: query.queryKey, exact: true });
}

/**
 * Keeps a player's cached profile, battles and statistics in step with the
 * battles stored for it.
 *
 * Their own cache times (minutes, and persisted across reloads with
 * consent) would otherwise keep showing the state from before a sync. An
 * open page polls the sync version, and every cached query older than the
 * newest version known is refetched: when that version rises, and when a
 * response arrives that was computed before it (a request that started just
 * before the sync). The versions are compared instead of keyed on, so the
 * statistics do not wait for the profile, which can fail on its own
 * (PROFILE_NOT_SYNCED), and data of the current version stays cached however
 * old it is.
 *
 * @param playerTag - The player as in the query keys (the URL parameter)
 * @param profileVersion - The profile's syncInfo.syncVersion, if loaded
 */
export function useSyncVersionRefresh(
  playerTag: string,
  profileVersion: number | undefined,
) {
  const queryClient = useQueryClient();
  const { data: polledVersion, dataUpdatedAt: polledAt } = useQuery({
    queryKey: ["syncVersion", playerTag],
    queryFn: () => fetchSyncVersion(playerTag),
    enabled: validatePlayerTagSyntax(playerTag),
    // Each poll has to ask the backend, a cached answer would not notice
    // anything
    staleTime: 0,
    refetchInterval: POLL_INTERVAL,
    refetchOnWindowFocus: false,
    retry: false,
    // No storage meta, so never persisted (main.tsx)
  });

  const versions = [profileVersion, polledVersion].filter(
    (v): v is number => v !== undefined,
  );
  const latest = versions.length > 0 ? Math.max(...versions) : undefined;

  // Read by the cache listener, which outlives a render
  const latestRef = useRef(latest);
  latestRef.current = latest;
  // Per query, the version it was last invalidated for
  const invalidatedFor = useRef(new Map<string, number>());
  useEffect(() => {
    invalidatedFor.current.clear();
  }, [playerTag]);

  // The version rose, or a poll answered: every cached query of the player
  // is compared. Each poll also retries the refetches that failed since.
  useEffect(() => {
    if (latest === undefined) return;
    for (const query of queryClient.getQueryCache().getAll()) {
      reconcile(queryClient, query, playerTag, latest, invalidatedFor.current);
    }
  }, [playerTag, latest, polledAt, queryClient]);

  // A response arrived: compared on its own, since it may have been
  // computed before the version that is already known. A query that lost
  // its last observer, such as battles left with older pages loaded, is
  // compared as well.
  useEffect(() => {
    return queryClient.getQueryCache().subscribe((event) => {
      if (event.type === "updated" && event.action.type === "error") {
        invalidatedFor.current.delete(event.query.queryHash);
        return;
      }
      const arrived =
        event.type === "updated" && event.action.type === "success";
      if (!arrived && event.type !== "observerRemoved") return;
      const known = latestRef.current;
      if (known === undefined) return;
      // After the cache finished its own update
      queueMicrotask(() =>
        reconcile(
          queryClient,
          event.query,
          playerTag,
          known,
          invalidatedFor.current,
        ),
      );
    });
  }, [playerTag, queryClient]);
}
