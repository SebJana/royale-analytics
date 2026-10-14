type QueryErrorState = {
  isError: boolean;
  isLoading: boolean;
  isFetching: boolean;
  data: unknown;
  errorUpdateCount: number;
};

/**
 * The error state a player page shows for a query: failed only while there
 * is nothing to show, and still failed while it retries.
 *
 * A query that never had data goes back to pending on a refetch, so the page
 * would swap its error for the loading state mid-retry and remount the error
 * afterwards, which resets PlayerError's retry attempts and jumps the layout
 * around. A failed background refresh (useSyncVersionRefresh.ts) keeps the
 * loaded data instead of replacing it with the error; the next poll tries
 * again.
 *
 * @param query - A useQuery or useInfiniteQuery result
 * @returns The same result with isError and isLoading as the page shows them
 */
export function pageQueryState<T extends QueryErrorState>(query: T): T {
  const retrying =
    !query.isError &&
    query.isFetching &&
    query.data === undefined &&
    query.errorUpdateCount > 0;
  if (retrying) return { ...query, isError: true, isLoading: false };
  if (query.isError && query.data !== undefined) {
    return { ...query, isError: false };
  }
  return query;
}
