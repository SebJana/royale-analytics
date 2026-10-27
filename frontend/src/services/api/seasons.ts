import api from "./axios";
import type { Season, TimeRange } from "../../types/seasons";

/**
 * Fetches the newest seasons, newest first. The backend decides how many,
 * so the filter offers exactly what it returns.
 *
 * @returns The seasons with their UTC boundaries
 */
export async function fetchSeasons(): Promise<Season[]> {
  const response = await api.get<Season[]>("/seasons");
  return response.data;
}

/**
 * Sets the timespan parameters of a statistics request: the season id, or
 * else the calendar days. The backend rejects both at once.
 * The timezone is sent either way, it groups the daily statistics.
 *
 * @param params - Query parameters of the request, changed in place
 * @param range - Timespan of the request
 */
export function setTimeRangeParams(params: URLSearchParams, range: TimeRange) {
  if (range.season) {
    params.set("season", range.season);
  } else {
    params.set("start_date", range.startDate);
    params.set("end_date", range.endDate);
  }

  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  params.set("timezone", timeZone);
}
