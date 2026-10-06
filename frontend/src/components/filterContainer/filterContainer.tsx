import { useState, useEffect } from "react";
import { GameModeFilter } from "../gameModeFilter/gameModeFilter";
import { StartEndDateFilter } from "../startEndDateFilter/startEndDateFilter";
import { CardFilter } from "../cardFilter/cardFilter";
import type { Card, CardMeta } from "../../types/cards";
import {
  getDateRange,
  getDefaultFilterState,
  setFilterStateToLocalStorage,
} from "../../utils/filter";
import { isValidDateRange } from "../../utils/datetime";
import "./filterContainer.css";

// TODO add visualSelectedGameModes here to selectedGameModes
export type FilterState = {
  startDate: string;
  endDate: string;
  gameModes: string[];
  cards: Card[];
  // Tower troop ids. At most one in Include mode.
  supportIds: number[];
  // Cards and tower troops no shown deck may contain, in both modes
  excludedCards: Card[];
  excludedSupportIds: number[];
  includeCardFilterMode: boolean;
  timespanOption: string;
  // Season id ("YYYY-MM") sent instead of the dates, which then only show
  // the season's days. null for a date range.
  season: string | null;
};

type FilterContainerProps = {
  gameModes: Record<string, string>;
  cards: CardMeta[];
  gameModesLoading: boolean;
  onFiltersApply: (filters: FilterState) => void;
  initialFilters?: Partial<FilterState>;
  showCardFilter?: boolean; // Optional prop to show/hide card filter
  // Optional prop to control wether the card filter works by definite inclusion (true) or closest match (false)
  includeCardFilterMode?: boolean;
  appliedFilters?: FilterState; // Current applied filters to sync UI
};

export function FilterContainer({
  gameModes,
  cards,
  gameModesLoading,
  onFiltersApply,
  initialFilters,
  showCardFilter,
  includeCardFilterMode,
  appliedFilters,
}: Readonly<FilterContainerProps>) {
  const initialDates = getDateRange();

  // Filter state management maintains two sets of state for each filter type:
  // 1. "selected" - what the user has chosen in the UI (not yet applied)
  // 2. "applied" - what is actually used for the API query
  // Use appliedFilters if available, otherwise fall back to initialFilters, then defaults
  const [selectedGameModes, setSelectedGameModes] = useState<string[]>(
    appliedFilters?.gameModes || initialFilters?.gameModes || [],
  );
  const [appliedGameModes, setAppliedGameModes] = useState<string[]>(
    appliedFilters?.gameModes || initialFilters?.gameModes || [],
  );

  const [selectedCards, setSelectedCards] = useState<Card[]>(
    showCardFilter ? appliedFilters?.cards || initialFilters?.cards || [] : [],
  );
  const [appliedCards, setAppliedCards] = useState<Card[]>(
    showCardFilter ? appliedFilters?.cards || initialFilters?.cards || [] : [],
  );

  const [selectedSupportIds, setSelectedSupportIds] = useState<number[]>(
    showCardFilter
      ? appliedFilters?.supportIds || initialFilters?.supportIds || []
      : [],
  );
  const [appliedSupportIds, setAppliedSupportIds] = useState<number[]>(
    showCardFilter
      ? appliedFilters?.supportIds || initialFilters?.supportIds || []
      : [],
  );

  const [selectedExcludedCards, setSelectedExcludedCards] = useState<Card[]>(
    showCardFilter
      ? appliedFilters?.excludedCards || initialFilters?.excludedCards || []
      : [],
  );
  const [appliedExcludedCards, setAppliedExcludedCards] = useState<Card[]>(
    showCardFilter
      ? appliedFilters?.excludedCards || initialFilters?.excludedCards || []
      : [],
  );

  const [selectedExcludedSupportIds, setSelectedExcludedSupportIds] = useState<
    number[]
  >(
    showCardFilter
      ? appliedFilters?.excludedSupportIds ||
          initialFilters?.excludedSupportIds ||
          []
      : [],
  );
  const [appliedExcludedSupportIds, setAppliedExcludedSupportIds] = useState<
    number[]
  >(
    showCardFilter
      ? appliedFilters?.excludedSupportIds ||
          initialFilters?.excludedSupportIds ||
          []
      : [],
  );

  const [selectedIncludeCardFilterMode, setSelectedIncludeCardFilterMode] =
    useState<boolean>(
      appliedFilters?.includeCardFilterMode ??
        initialFilters?.includeCardFilterMode ??
        includeCardFilterMode ??
        true,
    );
  const [appliedIncludeCardFilterMode, setAppliedIncludeCardFilterMode] =
    useState<boolean>(
      appliedFilters?.includeCardFilterMode ??
        initialFilters?.includeCardFilterMode ??
        includeCardFilterMode ??
        true,
    );

  const [selectedStartDate, setSelectedStartDate] = useState<string>(
    appliedFilters?.startDate ||
      initialFilters?.startDate ||
      initialDates.start,
  );
  const [appliedStartDate, setAppliedStartDate] = useState<string>(
    appliedFilters?.startDate ||
      initialFilters?.startDate ||
      initialDates.start,
  );

  const [selectedEndDate, setSelectedEndDate] = useState<string>(
    appliedFilters?.endDate || initialFilters?.endDate || initialDates.end,
  );
  const [appliedEndDate, setAppliedEndDate] = useState<string>(
    appliedFilters?.endDate || initialFilters?.endDate || initialDates.end,
  );

  const [selectedTimespanOption, setSelectedTimespanOption] = useState<string>(
    appliedFilters?.timespanOption ||
      initialFilters?.timespanOption ||
      "Last 7 days",
  );

  const [selectedSeason, setSelectedSeason] = useState<string | null>(
    appliedFilters?.season ?? initialFilters?.season ?? null,
  );
  const [appliedSeason, setAppliedSeason] = useState<string | null>(
    appliedFilters?.season ?? initialFilters?.season ?? null,
  );

  // Counts resets, so the timespan filter can switch back to the day options
  // even when no selected value changes
  const [resetCount, setResetCount] = useState(0);

  const [gameModesInitialized, setGameModesInitialized] = useState(false);
  const [applyButtonDisabled, setApplyButtonDisabled] = useState(true);

  // Sync UI state with applied filters when they change from parent
  useEffect(() => {
    if (appliedFilters) {
      setSelectedStartDate(appliedFilters.startDate);
      setSelectedEndDate(appliedFilters.endDate);
      setSelectedGameModes(appliedFilters.gameModes);
      setSelectedTimespanOption(appliedFilters.timespanOption);
      setSelectedSeason(appliedFilters.season ?? null);
      setSelectedIncludeCardFilterMode(appliedFilters.includeCardFilterMode);
      if (showCardFilter) {
        setSelectedCards(appliedFilters.cards);
        setSelectedSupportIds(appliedFilters.supportIds);
        setSelectedExcludedCards(appliedFilters.excludedCards);
        setSelectedExcludedSupportIds(appliedFilters.excludedSupportIds);
      }

      // Also update applied state to match
      setAppliedStartDate(appliedFilters.startDate);
      setAppliedEndDate(appliedFilters.endDate);
      setAppliedSeason(appliedFilters.season ?? null);
      setAppliedGameModes(appliedFilters.gameModes);
      setAppliedIncludeCardFilterMode(appliedFilters.includeCardFilterMode);
      if (showCardFilter) {
        setAppliedCards(appliedFilters.cards);
        setAppliedSupportIds(appliedFilters.supportIds);
        setAppliedExcludedCards(appliedFilters.excludedCards);
        setAppliedExcludedSupportIds(appliedFilters.excludedSupportIds);
      }
    }
  }, [appliedFilters, showCardFilter]);

  // Game mode initialization
  useEffect(() => {
    if (gameModes && !gameModesInitialized && !gameModesLoading) {
      // Keep an empty selection as the canonical "all modes" value. The
      // GameModeFilter renders it as "All game modes" without bonbons, and API
      // helpers omit the game_modes query parameter, so newly saved battle
      // modes are included even if the mode catalogue cache is briefly stale.
      setGameModesInitialized(true);
    }
  }, [gameModes, gameModesInitialized, gameModesLoading]);

  // Enable apply button when any selection differs from applied state
  useEffect(() => {
    const cardsChanged = showCardFilter
      ? JSON.stringify(appliedCards) !== JSON.stringify(selectedCards) ||
        JSON.stringify(appliedSupportIds) !==
          JSON.stringify(selectedSupportIds) ||
        JSON.stringify(appliedExcludedCards) !==
          JSON.stringify(selectedExcludedCards) ||
        JSON.stringify(appliedExcludedSupportIds) !==
          JSON.stringify(selectedExcludedSupportIds)
      : false;
    const cardFilterModeChanged =
      appliedIncludeCardFilterMode !== selectedIncludeCardFilterMode;

    // A season's days only show it, and the current one ends in the future
    const validDateRange =
      selectedSeason !== null ||
      isValidDateRange(selectedStartDate, selectedEndDate);

    if (
      (appliedStartDate !== selectedStartDate ||
        appliedEndDate !== selectedEndDate ||
        appliedSeason !== selectedSeason ||
        JSON.stringify(appliedGameModes) !==
          JSON.stringify(selectedGameModes) ||
        cardsChanged ||
        cardFilterModeChanged) &&
      validDateRange
    ) {
      setApplyButtonDisabled(false);
    } else {
      setApplyButtonDisabled(true);
    }
  }, [
    appliedStartDate,
    appliedEndDate,
    appliedSeason,
    appliedGameModes,
    appliedCards,
    appliedSupportIds,
    appliedExcludedCards,
    appliedExcludedSupportIds,
    appliedIncludeCardFilterMode,
    selectedStartDate,
    selectedEndDate,
    selectedSeason,
    selectedGameModes,
    selectedCards,
    selectedSupportIds,
    selectedExcludedCards,
    selectedExcludedSupportIds,
    selectedIncludeCardFilterMode,
    showCardFilter,
  ]);

  // Apply all filters and notify parent
  const applyAllFilters = () => {
    const newFilters: FilterState = {
      startDate: selectedStartDate,
      endDate: selectedEndDate,
      gameModes: selectedGameModes,
      cards: showCardFilter ? selectedCards : [], // Only include cards if card filter is enabled
      supportIds: showCardFilter ? selectedSupportIds : [],
      excludedCards: showCardFilter ? selectedExcludedCards : [],
      excludedSupportIds: showCardFilter ? selectedExcludedSupportIds : [],
      includeCardFilterMode: selectedIncludeCardFilterMode,
      timespanOption: selectedTimespanOption,
      season: selectedSeason,
    };

    setAppliedStartDate(selectedStartDate);
    setAppliedEndDate(selectedEndDate);
    setAppliedSeason(selectedSeason);
    setAppliedGameModes(selectedGameModes);
    setAppliedIncludeCardFilterMode(selectedIncludeCardFilterMode);
    if (showCardFilter) {
      setAppliedCards(selectedCards);
      setAppliedSupportIds(selectedSupportIds);
      setAppliedExcludedCards(selectedExcludedCards);
      setAppliedExcludedSupportIds(selectedExcludedSupportIds);
    }

    // Save the current state of the filter to the local Storage
    setFilterStateToLocalStorage(newFilters);

    onFiltersApply(newFilters);
  };

  // Reset clears the selection, user still has to click apply for the default to take effect
  const resetAllFilters = () => {
    const defaultFilters = getDefaultFilterState();
    setResetCount((count) => count + 1);

    setSelectedStartDate(defaultFilters.startDate);
    setSelectedEndDate(defaultFilters.endDate);
    setSelectedTimespanOption(defaultFilters.timespanOption);
    setSelectedSeason(defaultFilters.season);
    setSelectedGameModes(defaultFilters.gameModes);
    setSelectedIncludeCardFilterMode(defaultFilters.includeCardFilterMode);
    if (showCardFilter) {
      setSelectedCards(defaultFilters.cards);
      setSelectedSupportIds(defaultFilters.supportIds);
      setSelectedExcludedCards(defaultFilters.excludedCards);
      setSelectedExcludedSupportIds(defaultFilters.excludedSupportIds);
    }
  };

  return (
    // The scroll-to-top button stays hidden until this panel is scrolled
    // past, so it never covers the sticky Apply row
    <div className="filter-component-container" data-hides-scroll-to-top>
      <h2 className="filter-component-header">Filters</h2>
      <StartEndDateFilter
        selectedStart={selectedStartDate}
        selectedEnd={selectedEndDate}
        selectedOption={selectedTimespanOption}
        selectedSeason={selectedSeason}
        onStartChange={setSelectedStartDate}
        onEndChange={setSelectedEndDate}
        onOptionChange={setSelectedTimespanOption}
        onSeasonChange={setSelectedSeason}
        resetCount={resetCount}
      />
      <GameModeFilter
        gameModes={gameModes}
        selected={selectedGameModes}
        onChange={setSelectedGameModes}
      />
      {showCardFilter && (
        <CardFilter
          cards={cards}
          selected={selectedCards}
          onCardsChange={setSelectedCards}
          selectedSupportIds={selectedSupportIds}
          onSupportIdsChange={setSelectedSupportIds}
          excluded={selectedExcludedCards}
          onExcludedChange={setSelectedExcludedCards}
          excludedSupportIds={selectedExcludedSupportIds}
          onExcludedSupportIdsChange={setSelectedExcludedSupportIds}
          includeCardFilterMode={selectedIncludeCardFilterMode}
          onCardFilterModeChange={setSelectedIncludeCardFilterMode}
        />
      )}
      {/* Sticks to the bottom of the screen while the panel's end is below
          it, e.g. behind an expanded card filter, so Apply stays in reach
          without a second pair of buttons */}
      <div className="filter-component-button-container">
        <button
          type="button"
          className="filter-component-apply-button"
          onClick={applyAllFilters}
          disabled={applyButtonDisabled}
        >
          Apply
        </button>
        <button
          type="button"
          className="filter-component-reset-button filter-component-action-button"
          onClick={resetAllFilters}
        >
          Reset
        </button>
      </div>
    </div>
  );
}
