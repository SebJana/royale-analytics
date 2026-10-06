import { useState } from "react";
import { formatDateForInput, formatDateRange } from "../../utils/datetime";
import { ChevronUp } from "lucide-react";
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
 * 2. Custom date selection with manual date inputs
 *
 * Props:
 * - selectedStart/selectedEnd: Current date values (controlled by parent)
 * - selectedOption: Currently selected preset option (controlled by parent)
 * - onStartChange/onEndChange: Callbacks when dates change
 * - onOptionChange: Callback when preset option changes
 */
export function StartEndDateFilter({
  selectedStart,
  selectedEnd,
  selectedOption,
  onStartChange,
  onEndChange,
  onOptionChange,
}: Readonly<{
  selectedStart: string;
  selectedEnd: string;
  selectedOption: string;
  onStartChange: (next: string) => void; // emit date string
  onEndChange: (next: string) => void; // emit date string
  onOptionChange: (option: string) => void; // emit selected option
}>) {
  // Only manages UI expansion state - all filter values are controlled by parent
  const [isExpanded, setIsExpanded] = useState(false);

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

    // If it's a preset with days, calculate and set the date range
    if (option.days) {
      handleDateRangeSelection(option.days);
    }
    // If it's custom, don't change dates - let user manually select
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
        {/* Quick selection options */}
        <div className="start-end-date-filter-component-tags">
          {DATE_RANGE_OPTIONS.map((option) => (
            <button
              key={option.label}
              type="button"
              className={`start-end-date-filter-component-tag ${
                selectedOption === option.label ? "is-selected" : ""
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
      </div>
    </div>
  );
}
