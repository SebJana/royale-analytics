import { useParams } from "react-router-dom";
import type { DailyStats } from "../../types/dailyStats";
import { useDailyStats } from "../../hooks/useDailyStats";
import { usePageLoadingState } from "../../hooks/usePageLoadingState";
import CircularProgress from "@mui/material/CircularProgress";
import Tooltip from "@mui/material/Tooltip";
import { useGameModes } from "../../hooks/useGameModes";
import { getCurrentFilterState } from "../../utils/filter";
import { gameModesForQuery } from "../../utils/gameModes";
import { useEffect, useState } from "react";
import { ScrollToTopButton } from "../../components/scrollToTop/scrollToTop";
import type { DateLevel } from "../../types/chart";
import { LineChart } from "../../components/lineChart/lineChart";
import { PlayerError } from "../../components/playerError/playerError";
import { buildPlotConfig, PLOT_DEFINITIONS } from "../../utils/plotConfig";
import type { PlotDefinition } from "../../utils/plotConfig";
import { ArrowDown, ArrowUp } from "lucide-react";
import { FilterContainer } from "../../components/filterContainer/filterContainer";
import { SegmentedControl } from "../../components/segmentedControl/segmentedControl";
import type { FilterState } from "../../components/filterContainer/filterContainer";
import "./plots.css";

type DateSpacing = "calendar" | "recorded";

const DATE_LEVELS: DateLevel[] = ["year", "month", "day"];

function PlotPanel({
  definition,
  stats,
  startDate,
  endDate,
}: Readonly<{
  definition: PlotDefinition;
  stats: DailyStats;
  startDate: string;
  endDate: string;
}>) {
  const [dateLevel, setDateLevel] = useState<DateLevel>("day");
  const [dateSpacing, setDateSpacing] = useState<DateSpacing>("recorded");
  const levelIndex = DATE_LEVELS.indexOf(dateLevel);
  const config = buildPlotConfig(definition, stats, dateLevel);
  const previousLevel = DATE_LEVELS[levelIndex - 1];
  const nextLevel = DATE_LEVELS[levelIndex + 1];

  return (
    <section
      className="stat-chart"
      aria-labelledby={`plot-${definition.id}-title`}
    >
      <div className="plot-controls">
        <div
          className="plots-drill-controls"
          role="group"
          aria-label={`${config.title} date hierarchy`}
        >
          <Tooltip
            arrow
            title={
              previousLevel ? `Drill up to ${previousLevel}` : "Already at year"
            }
          >
            <span className="plots-drill-button-wrap">
              <button
                type="button"
                disabled={!previousLevel}
                onClick={() => setDateLevel(previousLevel)}
                aria-label="Drill up"
              >
                <ArrowUp size={18} />
              </button>
            </span>
          </Tooltip>
          <span className="plots-drill-level" aria-live="polite">
            {dateLevel.charAt(0).toUpperCase() + dateLevel.slice(1)}
          </span>
          <Tooltip
            arrow
            title={nextLevel ? `Drill down to ${nextLevel}` : "Already at day"}
          >
            <span className="plots-drill-button-wrap">
              <button
                type="button"
                disabled={!nextLevel}
                onClick={() => setDateLevel(nextLevel)}
                aria-label="Drill down"
              >
                <ArrowDown size={18} />
              </button>
            </span>
          </Tooltip>
        </div>
        <SegmentedControl
          ariaLabel="Date spacing"
          options={[
            { value: "recorded", label: "Recorded" },
            { value: "calendar", label: "Calendar" },
          ]}
          value={dateSpacing}
          onChange={setDateSpacing}
          tooltip={
            dateSpacing === "calendar"
              ? "Calendar spacing; dots mark missing periods"
              : "Even spacing for recorded periods"
          }
        />
      </div>
      <h2 className="plot-title" id={`plot-${definition.id}-title`}>
        {config.title}
      </h2>
      <LineChart
        className="plot-canvas"
        config={config}
        dateSpacing={dateSpacing}
        dateLevel={dateLevel}
        startDate={startDate}
        endDate={endDate}
      />
    </section>
  );
}

export default function PlayerPlots() {
  const { playerTag = "" } = useParams();
  // Filter state management maintains two sets of state for each filter type:
  // 1. "selected" - what the user has chosen in the UI (not yet applied)
  // 2. "applied" - what is actually used for the API query (in case of cards for the frontend filter)
  // This allows users to configure multiple filters before applying them all at once to reduce API calls and loading times

  // State to store applied filters from FilterContainer
  const [appliedFilters, setAppliedFilters] = useState<FilterState>(
    getCurrentFilterState(),
  );

  // Prevents double API calls during initialization, because filter and query need to be built on API Game Modes Data
  const [gameModesInitialized, setGameModesInitialized] = useState(false);

  const {
    data: gameModes,
    isLoading: gameModesLoading,
    isError: isGameModesError,
    refetch: refetchGameModes,
  } = useGameModes();

  // Game mode initialization
  // Initialize selected game modes once when game modes are loaded
  // An empty selection means all modes and deliberately omits game_modes from
  // the request. Do not expand it to the current list of mode keys: a new mode
  // can exist in battle data before it appears in the cached mode list.
  useEffect(() => {
    if (gameModes && !gameModesInitialized && !gameModesLoading) {
      setGameModesInitialized(true);
    }
  }, [gameModes, gameModesInitialized, gameModesLoading]);

  const handleFiltersApply = (filters: FilterState) => {
    setAppliedFilters(filters);
    // The API queries will automatically re-run when appliedFilters changes
  };

  // Card statistics API call
  // Fetch card statistics only when game modes are properly initialized
  // Uses applied filter values (not selected ones) to ensure query stability
  // Passes null for game modes to disable the query until gameModesInitialized is true
  const queryGameModes = gameModesInitialized
    ? gameModesForQuery(appliedFilters.gameModes, gameModes)
    : null;
  const {
    data: stats,
    isLoading: statsLoading,
    isError: isStatsError,
    refetch: refetchStats,
  } = useDailyStats(
    playerTag,
    appliedFilters.startDate,
    appliedFilters.endDate,
    queryGameModes,
  );

  // Use the modes actually sent to the API for the loading state dependency.
  const modesKey = queryGameModes?.join("|") ?? "";

  // Loading state management
  // Determines when to show loading spinner vs content
  // Uses a custom hook that tracks multiple loading states and prevents flickering
  const { isInitialLoad } = usePageLoadingState({
    loadingStates: [statsLoading, gameModesLoading],
    errorStates: [isStatsError, isGameModesError],
    hasData: () => Boolean(stats && stats.daily_statistics.daily.length > 0),
    // Reset dependency ensures loading state recalculates when any backend filter changes
    resetDependency: `${playerTag}-${appliedFilters.startDate}-${appliedFilters.endDate}-${modesKey}`,
  });

  if (isStatsError || isGameModesError) {
    return (
      <PlayerError
        sources={[
          { label: "statistics", failed: isStatsError, retry: refetchStats },
          {
            label: "game modes",
            failed: isGameModesError,
            retry: refetchGameModes,
          },
        ]}
      />
    );
  }

  return (
    <div className="plots-page">
      <div className="plots-content">
        {/* Loading State - Shows during initial load, cards loading, card stats loading, or game mode loading */}
        {/* The loading spinner prevents users from seeing incomplete data during the initialization process */}
        {(isInitialLoad || statsLoading || gameModesLoading) && (
          <div>
            <CircularProgress className="plots-loading-spinner" />
            <p>Loading statistics...</p>
          </div>
        )}
        {/* Loaded State - Show decks when all data is available and no errors occurred */}
        {!isInitialLoad && (
          <>
            {/* FilterContainer component */}
            <FilterContainer
              gameModes={gameModes || {}}
              cards={[]}
              gameModesLoading={gameModesLoading}
              onFiltersApply={handleFiltersApply}
              showCardFilter={false}
              appliedFilters={appliedFilters}
              initialFilters={getCurrentFilterState()}
            />
            {/* Show stats if there is any data to display */}
            {stats && stats.daily_statistics.daily.length > 0 && (
              <div className="plots-charts">
                {PLOT_DEFINITIONS.map((definition) => (
                  <PlotPanel
                    key={definition.id}
                    definition={definition}
                    stats={stats}
                    startDate={appliedFilters.startDate}
                    endDate={appliedFilters.endDate}
                  />
                ))}
              </div>
            )}
            <ScrollToTopButton />
            {/* Show message when no stats are found and not still loading */}
            {(!stats || stats.daily_statistics.daily.length === 0) &&
              !statsLoading &&
              !gameModesLoading &&
              !statsLoading && (
                <div className="no-plots-message">
                  <p>No statistics found with the current filters applied</p>
                </div>
              )}
          </>
        )}
      </div>
    </div>
  );
}
