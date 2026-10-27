import { formatDateForInput } from "./datetime";
import type { Season, TimeRange } from "../types/seasons";
import type { FilterState } from "../components/filterContainer/filterContainer";

// Fixed English abbreviations, so a label reads the same in every locale
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * Display name of a season id: "2026-09" becomes "2026-Sep".
 *
 * @param seasonId - Season id as "YYYY-MM"
 * @returns The label, or the id itself if it is malformed
 */
export function formatSeasonLabel(seasonId: string): string {
  const [year, month] = seasonId.split("-");
  const name = MONTHS[Number(month) - 1];
  return name ? `${year}-${name}` : seasonId;
}

/**
 * Calendar days a season covers in the browser's timezone, for display only.
 * The requests send the season id, whose exact window the backend resolves.
 *
 * @param season - Season from /seasons
 * @returns First and last day, YYYY-MM-DD, the last one inclusive
 */
export function getSeasonDateRange(season: Season) {
  // The end is exclusive, so its last day is the one of the instant before
  const lastInstant = new Date(new Date(season.end).getTime() - 1);
  return {
    start: formatDateForInput(new Date(season.start)),
    end: formatDateForInput(lastInstant),
  };
}

/**
 * Days of a season in the browser's timezone, with the same English month
 * names as the season labels: "Sep 7 - Oct 4". The current season's end is
 * still ahead, so it shows its start only: e.g. "Since Oct 5".
 *
 * @param season - Season from /seasons
 * @returns The days as text
 */
export function formatSeasonDates(season: Season): string {
  const { start, end } = getSeasonDateRange(season);
  const formatDay = (value: string) => {
    const [, month, day] = value.split("-").map(Number);
    return `${MONTHS[month - 1]} ${day}`;
  };
  if (season.isCurrent) return `Since ${formatDay(start)}`;
  return `${formatDay(start)} - ${formatDay(end)}`;
}

/**
 * Timespan of the statistics requests for applied filters.
 *
 * @param filters - Applied filter state
 * @returns The dates and the season, if one is selected
 */
export function getTimeRange(filters: FilterState): TimeRange {
  return {
    startDate: filters.startDate,
    endDate: filters.endDate,
    season: filters.season ?? null,
  };
}
