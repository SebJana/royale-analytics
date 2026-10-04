import { formatDateForInput } from "./datetime";
import type { FilterState } from "../components/filterContainer/filterContainer";

// NOTE: Default day value HAS to exist as option of StartEndDateFilter
// If this value isn't an option, it will still be the value used in the queries (applied filter)
// but it WON'T be highlighted in the filter UI, no option will be shown as selected
const DEFAULT_DAY_RANGE = 7;

/**
 * Saves filter state to localStorage.
 * Serializes the FilterState object to JSON and stores it under the "filterState" key.
 * Also saves the current timestamp of the last filter save to local storage under the "filterStateLastUpdated" key.
 *
 * @param filters - The filter state object to persist
 */
export function setFilterStateToLocalStorage(filters: FilterState) {
  localStorage.setItem("filterState", JSON.stringify(filters));
  localStorage.setItem("filterStateLastUpdated", JSON.stringify(Date.now()));
}

/**
 * Retrieves and parses filter state from localStorage.
 * Attempts to parse the stored JSON data back into a FilterState object.
 *
 * @returns The parsed FilterState object if found and valid and not older than the defined TTL, null otherwise
 * @throws Logs an error to console if JSON parsing fails, but returns null instead of throwing
 */
export function getFilterStateFromLocalStorage(): FilterState | null {
  // TTL of the filter state in seconds (7 days)
  const FILTER_TTL_SECONDS = 7 * 24 * 60 * 60;

  try {
    // Read raw filter state from localStorage
    const rawFilters = localStorage.getItem("filterState");
    if (!rawFilters) return null;

    // Read last updated timestamp
    const lastUpdated = Number(localStorage.getItem("filterStateLastUpdated"));
    if (!lastUpdated) return null;

    // Compute how many seconds have passed since last update
    const now = Date.now();
    const diffSeconds = (now - lastUpdated) / 1000;

    // If within the TTL, return the parsed filter state
    if (diffSeconds <= FILTER_TTL_SECONDS) {
      return migrateFilterState(JSON.parse(rawFilters) as FilterState);
    }

    // Otherwise treat as expired
    return null;
  } catch (error) {
    console.error("Failed to parse filter state from localStorage:", error);
    return null;
  }
}

/**
 * Brings a saved filter state up to the current shape. States saved before
 * tower troops have no supportIds. Include mode allows one tower troop, so a
 * longer list keeps its most recently selected entry.
 *
 * @param filters - Filter state as parsed from localStorage
 * @returns The filter state with valid supportIds
 */
function migrateFilterState(filters: FilterState): FilterState {
  const supportIds = Array.isArray(filters.supportIds)
    ? filters.supportIds.filter((id) => Number.isInteger(id))
    : [];
  return {
    ...filters,
    supportIds:
      filters.includeCardFilterMode !== false
        ? supportIds.slice(-1)
        : supportIds,
  };
}

/**
 * Calculates initial date range for filters.
 * Sets the end date to today and start date to dayOffset (default: DEFAULT_DAY_RANGE) days ago.
 *
 * @returns Object containing formatted start and end date strings in YYYY-MM-DD format
 */
export function getDateRange(dayOffset: number = DEFAULT_DAY_RANGE) {
  const endDate = new Date();
  const startDate = new Date();
  // Initialize with last dayOffset days as default date range
  startDate.setDate(startDate.getDate() - dayOffset);
  return {
    start: formatDateForInput(startDate),
    end: formatDateForInput(endDate),
  };
}

/**
 * Extracts the first integer found in a string.
 *
 * @param str - The input string to search.
 * @returns The first integer found, or null if none exists.
 */
function extractFirstNumber(str: string): number | null {
  const regex = /\d+/;
  const numberMatch = regex.exec(str);

  if (!numberMatch) {
    return null;
  }

  const numberValue = Number.parseInt(numberMatch[0], 10);
  return numberValue;
}

/**
 * Default configuration for the filters
 *
 * Default fallback configuration:
 * - Date range: Last DEFAULT_DAY_RANGE days (from today)
 * - Game modes: Empty array (no filters applied)
 * - Cards: Empty array (no filters applied)
 * - Tower troops: Empty array (no filters applied)
 * - Card inclusion filter mode: true, meaning all selected cards HAVE to be included in the shown decks
 * - Timespan option: "Last DEFAULT_DAY_RANGE days"
 *
 * @returns FilterState object with either restored or default filter values

 */
export function getDefaultFilterState(): FilterState {
  // Return default filter state
  const initialDates = getDateRange(DEFAULT_DAY_RANGE);
  return {
    startDate: initialDates.start,
    endDate: initialDates.end,
    gameModes: [],
    cards: [],
    supportIds: [],
    includeCardFilterMode: true,
    timespanOption: `Last ${DEFAULT_DAY_RANGE} days`,
  };
}

/**
 * Creates an initial filter state, prioritizing saved state from localStorage with smart date handling.
 * This function is used to persist filter states over different pages and page visits.
 *
 * Behavior:
 * 1. If filters exist in localStorage:
 *    - For non-"Custom" timespan options: Recalculates dates based on the saved timespan
 *    - For "Custom" timespan: Uses the exact saved dates
 *    - Preserves saved game modes and cards
 * 2. If no saved filters: Returns default filter state
 *
 * @returns FilterState object with either restored or default filter values
 */
export function getCurrentFilterState(): FilterState {
  const filters = getFilterStateFromLocalStorage();
  // Check if there are filters saved
  if (filters) {
    // If timespan is NOT custom, calculate start and end date new, because last X days
    // might mean a different time span, now that possibly the day the user uses the site on changed
    if (filters.timespanOption !== "Custom") {
      // Extract which Last X days option is selected (Extract the X)
      // Fall back to specified value if no day amount could be extracted
      const days =
        extractFirstNumber(filters.timespanOption) ?? DEFAULT_DAY_RANGE;
      const newlyCalcDates = getDateRange(days);

      // Return updated filter state, with new start and end date
      return {
        startDate: newlyCalcDates.start,
        endDate: newlyCalcDates.end,
        gameModes: filters.gameModes,
        cards: filters.cards,
        supportIds: filters.supportIds,
        includeCardFilterMode: filters.includeCardFilterMode,
        timespanOption: filters.timespanOption,
      };
    }
    // Return (unchanged) saved filter state
    return filters;
  }

  // Return default filter state
  return getDefaultFilterState();
}
