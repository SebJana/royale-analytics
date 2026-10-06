import { useEffect, useState } from "react";
import { formatDateForInput, formatDateRange } from "../../utils/datetime";
import {
  formatSeasonDates,
  formatSeasonLabel,
  getSeasonDateRange,
} from "../../utils/seasons";
import { useSeasons } from "../../hooks/useSeasons";
import type { Season } from "../../types/seasons";
import { ChevronUp } from "lucide-react";
import { SegmentedControl } from "../segmentedControl/segmentedControl";
import "./startEndDateFilter.css";

// DateRangeOption represents either a preset date range or custom selection
type DateRangeOption = {
  label: string; // Display name for the option
  days?: number; // Number of days back from today (undefined for custom)
  isCustom?: boolean; // Flag to identify custom date selection
};

// Predefined options for common date ranges plus custom selection
const DATE_RANGE_OPTIONS: DateRangeOption[] = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
  { label: "Last 365 days", days: 365 },
  { label: "Custom", isCustom: true }, // Shows date inputs when selected
];

/**
 * A collapsible filter component that allows users to select date ranges either:
 * 1. From preset options (Last 7 days, 30 days, etc.)
 * 2. From the newest seasons the backend offers
 * 3. Custom date selection with manual date inputs
 *
 * Props:
 * - selectedStart/selectedEnd: Current date values (controlled by parent)
 * - selectedOption: Currently selected preset option (controlled by parent)
 * - selectedSeason: Selected season id, null for a date range
 * - onStartChange/onEndChange: Callbacks when dates change
 * - onOptionChange: Callback when preset option changes
 * - onSeasonChange: Callback when the season changes
 * - resetCount: Changes on every reset of the filters
 */
export function StartEndDateFilter({
  selectedStart,
  selectedEnd,
  selectedOption,
  selectedSeason,
  onStartChange,
  onEndChange,
  onOptionChange,
  onSeasonChange,
  resetCount,
}: Readonly<{
  selectedStart: string;
  selectedEnd: string;
  selectedOption: string;
  selectedSeason: string | null;
  onStartChange: (next: string) => void; // emit date string
  onEndChange: (next: string) => void; // emit date string
  onOptionChange: (option: string) => void; // emit selected option
  onSeasonChange: (season: string | null) => void; // emit season id
  resetCount: number;
}>) {
  // Only manages UI expansion state - all filter values are controlled by parent
  const [isExpanded, setIsExpanded] = useState(false);
  const { data: seasons = [], isError: seasonsError } = useSeasons();
  // Which options show. Only a click on one of them changes the selection.
  const [isSeasonMode, setIsSeasonMode] = useState(selectedSeason !== null);
  // Applied filters from the parent switch to the matching options. A reset
  // does too: after browsing the seasons without picking one, the season
  // stays null, so only the reset count changes.
  useEffect(() => {
    setIsSeasonMode(selectedSeason !== null);
  }, [selectedSeason, resetCount]);
  // A saved season that is no longer among the newest still applies, so it
  // keeps a selected tag
  const isListedSeason = seasons.some((season) => season.id === selectedSeason);

  // Calculates start/end dates for preset options (7 days, 30 days, etc.)
  const handleDateRangeSelection = (days: number) => {
    const endDate = new Date(); // Today
    const startDate = new Date();
    startDate.setDate(startDate.getDate() - days); // X days ago

    // Notify parent component of date changes
    onStartChange(formatDateForInput(startDate));
    onEndChange(formatDateForInput(endDate));
  };

  // Handles clicks on preset options and custom selection
  const handleOptionClick = (option: DateRangeOption) => {
    // Always update the selected option in parent
    onOptionChange(option.label);
    onSeasonChange(null);

    // If it's a preset with days, calculate and set the date range
    if (option.days) {
      handleDateRangeSelection(option.days);
    }
    // If it's custom, don't change dates - let user manually select. Only
    // the end of the current season, which lies ahead, is pulled back to
    // today, since a date range can not end in the future.
    if (option.isCustom) {
      const today = formatDateForInput(new Date());
      if (selectedEnd > today) onEndChange(today);
    }
  };

  // The days only show the season, the requests send its id
  const handleSeasonClick = (season: Season) => {
    const { start, end } = getSeasonDateRange(season);
    onStartChange(start);
    onEndChange(end);
    // The summary names it a season, a bare "2026-Oct" could read as a month
    onOptionChange(`Season ${formatSeasonLabel(season.id)}`);
    onSeasonChange(season.id);
  };

  return (
    <div className="start-end-date-filter-container">
      {/* Click to expand/collapse filter options */}
      <button
        type="button"
        className="start-end-date-filter-component-header"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <span className="start-end-date-filter-component-title">Timespan</span>
        <ChevronUp
          className={`start-end-date-filter-component-toggle ${
            !isExpanded ? "collapsed" : ""
          }`}
        />
      </button>
      {/* Under the title like the selected game modes and cards, so the
          active range stays readable while collapsed. Unlike all game modes,
          every timespan narrows the data, so the default shows too. */}
      <div className="start-end-date-filter-component-summary">
        <span className="start-end-date-filter-component-bonbon">
          {selectedOption === "Custom"
            ? (formatDateRange(selectedStart, selectedEnd) ?? "Custom")
            : selectedOption}
        </span>
      </div>

      {/* Hidden/shown based on isExpanded state */}
      <div
        id="start-end-date-filter-component-options"
        className={`start-end-date-filter-component-grid ${
          !isExpanded ? "hidden" : ""
        }`}
      >
        {/* Days and seasons are two ways to pick the same timespan, only
            one of them applies */}
        <div className="start-end-date-filter-component-controls">
          <SegmentedControl
            title="Timespan by"
            ariaLabel="Timespan by"
            options={[
              { value: "days", label: "Days" },
              { value: "season", label: "Season" },
            ]}
            value={isSeasonMode ? "season" : "days"}
            onChange={(mode) => setIsSeasonMode(mode === "season")}
            hint={{
              days: "Recent or custom calendar days",
              season: "Monthly Clash Royale seasons",
            }}
          />
        </div>

        {isSeasonMode ? (
          <div className="start-end-date-filter-component-tags">
            {seasons.map((season) => (
              <button
                key={season.id}
                type="button"
                title={formatSeasonDates(season)}
                className={`start-end-date-filter-component-tag ${
                  selectedSeason === season.id ? "is-selected" : ""
                }`}
                onClick={() => handleSeasonClick(season)}
              >
                <span className="start-end-date-filter-component-tag-text">
                  {formatSeasonLabel(season.id)}
                  {season.isCurrent ? " (current)" : ""}
                </span>
              </button>
            ))}
            {selectedSeason !== null && !isListedSeason && (
              <button
                type="button"
                className="start-end-date-filter-component-tag is-selected"
              >
                <span className="start-end-date-filter-component-tag-text">
                  {formatSeasonLabel(selectedSeason)}
                </span>
              </button>
            )}
            {seasons.length === 0 && selectedSeason === null && (
              <p className="start-end-date-filter-component-empty">
                {seasonsError
                  ? "Seasons could not be loaded."
                  : "Loading seasons..."}
              </p>
            )}
          </div>
        ) : (
          <>
            <div className="start-end-date-filter-component-tags">
              {DATE_RANGE_OPTIONS.map((option) => (
                <button
                  key={option.label}
                  type="button"
                  className={`start-end-date-filter-component-tag ${
                    selectedSeason === null && selectedOption === option.label
                      ? "is-selected"
                      : ""
                  }`}
                  onClick={() => handleOptionClick(option)}
                >
                  <span className="start-end-date-filter-component-tag-text">
                    {option.label}
                  </span>
                </button>
              ))}
            </div>

            {/* Only shown when "Custom" option is selected - allows manual date selection for precise control */}
            {selectedOption === "Custom" && (
              <div className="start-end-date-filter-component-custom-inputs">
                <div className="start-end-date-filter-component-input-group">
                  <label
                    htmlFor="start-date"
                    className="start-end-date-filter-component-label"
                  >
                    Start Date:
                  </label>
                  <input
                    id="start-date"
                    type="date"
                    value={selectedStart}
                    onChange={(e) => onStartChange(e.target.value)}
                    className="start-end-date-filter-component-input"
                  />
                </div>

                <div className="start-end-date-filter-component-input-group">
                  <label
                    htmlFor="end-date"
                    className="start-end-date-filter-component-label"
                  >
                    End Date:
                  </label>
                  <input
                    id="end-date"
                    type="date"
                    value={selectedEnd}
                    onChange={(e) => onEndChange(e.target.value)}
                    className="start-end-date-filter-component-input"
                  />
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
