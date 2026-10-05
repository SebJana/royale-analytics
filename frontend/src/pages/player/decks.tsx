import { useCards } from "../../hooks/useCards";
import { useParams } from "react-router-dom";
import { useDeckStats } from "../../hooks/useDeckStats";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { DeckComponent } from "../../components/deck/deck";
import { PlayerError } from "../../components/playerError/playerError";
import { usePageLoadingState } from "../../hooks/usePageLoadingState";
import CircularProgress from "@mui/material/CircularProgress";
import { useGameModes } from "../../hooks/useGameModes";
import { formatNumber, round } from "../../utils/number";
import { pluralize } from "../../utils/plural";
import { getCurrentFilterState } from "../../utils/filter";
import {
  gameModesForQuery,
  mapInternalNameToDisplayName,
} from "../../utils/gameModes";
import { datetimeToLocale } from "../../utils/datetime";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { RefObject } from "react";
import { StatCard } from "../../components/statCard/statCard";
import { ScrollToTopButton } from "../../components/scrollToTop/scrollToTop";
import { FilterContainer } from "../../components/filterContainer/filterContainer";
import type { FilterState } from "../../components/filterContainer/filterContainer";
import { SortByContainer } from "../../components/sortByContainer/sortByContainer";
import type { Card, CardMeta } from "../../types/cards";
import type { Deck, DeckCardFilter, DeckSort } from "../../types/deckStats";
import { getCardFilterKey } from "../../utils/getCardMetaFields";
import "./decks.css";

// Minimum battles per deck the sort box offers. 1 shows every deck.
const MIN_BATTLE_OPTIONS = [1, 2, 3, 5, 10, 25, 50];

// The order of the first request. Its result tells whether every deck fits
// into the response; then the browser sorts and filters by itself.
const DEFAULT_SORT: DeckSort = {
  sortBy: "battleCount",
  sortOrder: "desc",
  minBattles: 1,
};

/**
 * Sorts decks like the backend does: by the given field, ties by battles and
 * then by last seen, both highest first.
 *
 * @param decks - Decks to sort, not changed
 * @param sort - Field and direction
 * @returns A sorted copy
 */
function sortDecksLikeBackend(decks: Deck[], sort: DeckSort): Deck[] {
  const direction = sort.sortOrder === "asc" ? 1 : -1;
  const compare = (a: number | string, b: number | string) =>
    a < b ? -1 : a > b ? 1 : 0;
  return [...decks].sort(
    (a, b) =>
      direction * compare(a[sort.sortBy], b[sort.sortBy]) ||
      compare(b.battleCount, a.battleCount) ||
      compare(b.lastSeen, a.lastSeen),
  );
}

// Helper type to rate/score the decks when using the card filter match mode
type DeckWithMatchScore = Deck & {
  matchPercentage: number;
  matchedCardCount: number;
};

// Type for deck sorting that includes actual Deck fields and computed fields
type DeckSortFields = {
  battleCount: number; // Direct field from Deck
  wins: number; // Direct field from Deck
  winRate: number; // Direct field from Deck
  usageRate: number; // Computed field
  lastSeen: string; // Direct field from Deck
};

function calculateAndFormatUsageRate(
  battleCount: number,
  totalBattles: number,
) {
  const usageRate = (battleCount / totalBattles) * 100; // In percent
  const roundedUsageRate = round(usageRate, 1);
  return `${roundedUsageRate}%`;
}

function GameModesStat({ modes }: Readonly<{ modes: string[] }>) {
  const modeCounts = new Map<string, number>();
  for (const mode of modes) {
    const name = mapInternalNameToDisplayName(mode);
    modeCounts.set(name, (modeCounts.get(name) ?? 0) + 1);
  }

  // Keep the tooltip short even when a deck was played in many different modes.
  const visibleModes = [...modeCounts].sort(([a], [b]) => a.localeCompare(b));
  const shownModes = visibleModes.slice(0, 8);
  const remainingModes = visibleModes
    .slice(8)
    .reduce((count, [, variants]) => count + variants, 0);

  return (
    <StatCard
      label={pluralize(modes.length, "Game Mode", "Game Modes")}
      value={modes.length}
      tooltip={
        modes.length > 0 ? (
          <div className="decks-game-modes-tooltip">
            <strong>Game modes in this deck</strong>
            <ul>
              {shownModes.map(([name, variants]) => (
                <li key={name}>
                  {name}
                  {variants > 1 && ` (${variants} variants)`}
                </li>
              ))}
            </ul>
            {remainingModes > 0 && <span>+{remainingModes} more modes</span>}
          </div>
        ) : (
          ""
        )
      }
    />
  );
}

function VirtualDeckList({
  decks,
  cards,
  totalBattles,
  showMatch,
  matchedCards,
  matchedSupportIds,
  scrollingToTopRef,
}: Readonly<{
  decks: (Deck | DeckWithMatchScore)[];
  cards: CardMeta[];
  totalBattles: number;
  showMatch: boolean;
  matchedCards: Card[];
  matchedSupportIds: number[];
  scrollingToTopRef: RefObject<boolean>;
}>) {
  const listRef = useRef<HTMLDivElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);

  // Only render deck rows near the visible part of the page.
  // Row heights are measured after rendering because they vary with screen width.
  const virtualizer = useWindowVirtualizer({
    count: decks.length, // Total number of decks the user can scroll through
    estimateSize: () => 420, // Initial row height in pixels, before its actual height is measured
    overscan: 3, // Render three extra rows above and below the visible area
    scrollMargin, // Distance from the top of the page to the start of the deck list
  });

  // Filtering can reuse a mounted row for a different deck. Measure it again
  // immediately so a stale row height does not shift decks into each other.
  // Scrolling a row out and back in does the same measurement on remount.
  const measureRow = useCallback(
    (element: HTMLDivElement | null) => {
      if (element && !decks[Number(element.dataset.index)]) return;
      virtualizer.measureElement(element);
    },
    [decks, virtualizer],
  );

  useLayoutEffect(() => {
    // Measuring rows above the viewport can interrupt the Back to Top animation.
    virtualizer.shouldAdjustScrollPositionOnItemSizeChange = (
      item,
      _delta,
      instance,
    ) =>
      !scrollingToTopRef.current && item.start < (instance.scrollOffset ?? 0);
    return () => {
      virtualizer.shouldAdjustScrollPositionOnItemSizeChange = undefined;
    };
  }, [virtualizer, scrollingToTopRef]);

  useLayoutEffect(() => {
    const updateScrollMargin = () => {
      if (listRef.current) {
        setScrollMargin(
          listRef.current.getBoundingClientRect().top + window.scrollY,
        );
      }
    };
    updateScrollMargin();
    // Expanding the filters moves the list without resizing the window.
    const resizeObserver = new ResizeObserver(updateScrollMargin);
    const content = listRef.current?.closest(".decks-content");
    if (content) {
      resizeObserver.observe(content);
      content
        .querySelectorAll(
          ".filter-component-container, .sort-by-container, .decks-general-stats",
        )
        .forEach((element) => resizeObserver.observe(element));
    }
    window.addEventListener("resize", updateScrollMargin);
    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", updateScrollMargin);
    };
  }, []);

  return (
    // Keep the full scroll height even though only nearby rows are rendered.
    <div
      ref={listRef}
      className="decks-virtual-list"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map((virtualRow) => {
        const d = decks[virtualRow.index];
        return (
          <div
            key={virtualRow.key}
            data-index={virtualRow.index}
            ref={measureRow}
            className="decks-deck-row"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              // virtualRow.start includes the list's page offset; CSS uses list coordinates.
              transform: `translateY(${virtualRow.start - scrollMargin}px)`,
            }}
          >
            <div className="deck-section">
              {showMatch && "matchPercentage" in d && (
                <div className="decks-card-match-header">
                  <span className="decks-card-match-value">{`${round(
                    d.matchPercentage,
                    1,
                  )}% (${d.matchedCardCount} matching ${pluralize(
                    d.matchedCardCount,
                    "Card",
                    "Cards",
                  )})`}</span>
                  <span className="decks-card-match-label">Card Match</span>
                </div>
              )}
              <DeckComponent
                deck={d.deck}
                support={d.support}
                // The player's own decks, so the tower sits on the left like
                // on the player's side of a battle
                supportSide="left"
                cards={cards}
                matchedCards={showMatch ? matchedCards : undefined}
                matchedSupportIds={showMatch ? matchedSupportIds : undefined}
              />
            </div>
            <div className="deck-stats-container">
              <StatCard
                label={pluralize(d.battleCount, "Battle", "Battles")}
                value={d.battleCount}
              />
              <StatCard
                label={pluralize(d.wins, "Win", "Wins")}
                value={d.wins}
              />
              <StatCard label="Win Rate" value={`${round(d.winRate, 1)}%`} />
              <StatCard
                label="Usage Rate"
                value={calculateAndFormatUsageRate(d.battleCount, totalBattles)}
              />
              <GameModesStat modes={d.modes} />
              <StatCard
                label="Last Seen"
                value={datetimeToLocale(d.lastSeen)}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// TODO The cards and plots pages should keep their filters on screen when
// their statistics route fails, like this page does for the decks

// TODO add same error handling for all pages if no data is found or the tag is invalid
export default function PlayerDecks() {
  const { playerTag = "" } = useParams();
  const scrollingToTopRef = useRef(false);
  const scrollResetTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  useEffect(() => {
    const resetAtTop = () => {
      if (window.scrollY <= 1) scrollingToTopRef.current = false;
    };
    const cancelScrollToTop = () => {
      scrollingToTopRef.current = false;
    };
    window.addEventListener("scroll", resetAtTop);
    window.addEventListener("wheel", cancelScrollToTop);
    window.addEventListener("touchstart", cancelScrollToTop);
    window.addEventListener("keydown", cancelScrollToTop);
    return () => {
      window.removeEventListener("scroll", resetAtTop);
      window.removeEventListener("wheel", cancelScrollToTop);
      window.removeEventListener("touchstart", cancelScrollToTop);
      window.removeEventListener("keydown", cancelScrollToTop);
      if (scrollResetTimeoutRef.current)
        clearTimeout(scrollResetTimeoutRef.current);
    };
  }, []);

  const handleScrollToTopStart = () => {
    scrollingToTopRef.current = true;
    if (scrollResetTimeoutRef.current)
      clearTimeout(scrollResetTimeoutRef.current);
    // Also restore normal scroll adjustments if the user interrupts the animation.
    scrollResetTimeoutRef.current = setTimeout(() => {
      scrollingToTopRef.current = false;
    }, 3000);
  };

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

  // Sort state management
  // Tracks which field to sort by and the sort direction
  const [selectedSortOption, setSelectedSortOption] =
    useState<keyof DeckSortFields>("battleCount"); // Default: sort by battles (most relevant)
  const [sortAscending, setSortAscending] = useState(false); // Default to descending (highest values first)
  // Decks played fewer times are left out, so e.g. a deck won once does not
  // top the win rate. Applied by the backend, like the sort.
  const [minBattles, setMinBattles] = useState(1);

  const {
    data: cards,
    isLoading: cardsLoading,
    isError: isCardsError,
    refetch: refetchCards,
  } = useCards();

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

  // Sort handler function - manages sort option and direction state
  // Clicking same option toggles direction, clicking different option resets to descending
  const handleSortChange = (nextSortOption: keyof DeckSortFields) => {
    if (nextSortOption === selectedSortOption) {
      // Same option clicked - toggle sort direction (ascending ↔ descending)
      setSortAscending(!sortAscending);
    } else {
      // Different option selected - change sort field and reset to descending (most useful default)
      setSelectedSortOption(nextSortOption);
      setSortAscending(false);
    }
  };

  // Available sort options for decks
  const sortOptions: (keyof DeckSortFields)[] = [
    "battleCount",
    "wins",
    "winRate",
    "usageRate",
    "lastSeen",
  ];

  // Every selected card is one match term, and the tower selection adds one
  // more. More selected towers widen that term instead of adding terms, so
  // they never lower a deck's score.
  const matchTermCount =
    appliedFilters.cards.length +
    (appliedFilters.supportIds.length > 0 ? 1 : 0);
  const showMatch = !appliedFilters.includeCardFilterMode && matchTermCount > 0;

  // The backend filters the decks by cards and tower troops:
  // 1) Include mode: decks HAVE to include ALL selected cards and the selected tower
  // 2) Match mode: decks need one of them and come ranked by how many they share
  // Excluded cards and towers drop a deck in both modes. Without any selection
  // the filter is left out, so the request is the one for all decks.
  const hasCardFilter =
    matchTermCount > 0 ||
    appliedFilters.excludedCards.length > 0 ||
    appliedFilters.excludedSupportIds.length > 0;
  // Sorted, so the same selection in another click order is the same query
  // and cache entry
  const cardFilter: DeckCardFilter | undefined = hasCardFilter
    ? {
        mode: appliedFilters.includeCardFilterMode ? "include" : "match",
        cards: appliedFilters.cards.map(getCardFilterKey).sort(),
        excludeCards: appliedFilters.excludedCards.map(getCardFilterKey).sort(),
        supportIds: [...appliedFilters.supportIds].sort((a, b) => a - b),
        excludeSupportIds: [...appliedFilters.excludedSupportIds].sort(
          (a, b) => a - b,
        ),
      }
    : undefined;

  // Deck statistics API call
  // Fetch deck statistics only when game modes are properly initialized
  // Uses applied filter values (not selected ones) to ensure query stability
  // Passes null for game modes to disable the query until gameModesInitialized is true
  const queryGameModes = gameModesInitialized
    ? gameModesForQuery(appliedFilters.gameModes, gameModes)
    : null;
  // Usage rate orders like battle count. Match mode ranks by matched cards
  // and disables the sort, so it keeps the default.
  const deckSort: DeckSort = showMatch
    ? DEFAULT_SORT
    : {
        sortBy:
          selectedSortOption === "usageRate"
            ? "battleCount"
            : selectedSortOption,
        sortOrder: sortAscending ? "asc" : "desc",
        minBattles,
      };
  // First request: the default order. If every deck of the filter context
  // fits into it (real players have 150-200 decks, the cap is 250), sorting
  // and the minimum battles run in the browser without another request.
  const base = useDeckStats(
    playerTag,
    appliedFilters.startDate,
    appliedFilters.endDate,
    queryGameModes,
    cardFilter,
    DEFAULT_SORT,
  );
  const baseStats = base.data?.deck_statistics;
  const allDecksLoaded =
    baseStats !== undefined && baseStats.decks.length >= baseStats.deckCount;
  // Only a capped result needs the backend for another order: its top decks
  // of that order may not be in the browser at all.
  const needsBackendSort =
    baseStats !== undefined &&
    !allDecksLoaded &&
    JSON.stringify(deckSort) !== JSON.stringify(DEFAULT_SORT);
  const sorted = useDeckStats(
    playerTag,
    appliedFilters.startDate,
    appliedFilters.endDate,
    queryGameModes,
    cardFilter,
    deckSort,
    needsBackendSort,
  );
  // Until the requested order arrives, the previous order stays on screen,
  // dimmed: the last backend sort, or the default order on the first one
  const sortPending =
    needsBackendSort &&
    !sorted.isError &&
    (sorted.isPlaceholderData || !sorted.data);
  const decksQuery = needsBackendSort && sorted.data ? sorted : base;
  const decksLoading = base.isLoading;
  const isDecksError = base.isError || (needsBackendSort && sorted.isError);
  // Both queries can fail on their own, and retrying only one would leave the
  // other's error on screen
  const refetchDecks = () =>
    Promise.allSettled([
      base.isError ? base.refetch() : undefined,
      needsBackendSort && sorted.isError ? sorted.refetch() : undefined,
    ]);

  // The shown decks and their totals: the backend's, or computed here when
  // every deck is in the browser
  const deckStats = (() => {
    const stats = decksQuery.data?.deck_statistics;
    if (!stats || needsBackendSort || !allDecksLoaded || showMatch) {
      return stats;
    }
    const kept = stats.decks.filter(
      (deck) => deck.battleCount >= deckSort.minBattles,
    );
    return {
      ...stats,
      decks: sortDecksLikeBackend(kept, deckSort),
      deckCount: kept.length,
      battleCount: kept.reduce((sum, deck) => sum + deck.battleCount, 0),
      wins: kept.reduce((sum, deck) => sum + deck.wins, 0),
    };
  })();

  // Match mode adds the score for the match header
  const filteredDecks = (() => {
    const decks = deckStats?.decks ?? [];
    if (!showMatch) return decks;
    return decks.map((deck) => ({
      ...deck,
      matchedCardCount: deck.matchedCardCount ?? 0,
      matchPercentage: ((deck.matchedCardCount ?? 0) / matchTermCount) * 100,
    }));
  })() as (Deck | DeckWithMatchScore)[];

  // Use the modes actually sent to the API for the loading state dependency.
  const modesKey = queryGameModes?.join("|") ?? "";
  // Every backend filter of the deck request
  const decksContextKey = `${playerTag}-${appliedFilters.startDate}-${appliedFilters.endDate}-${modesKey}-${JSON.stringify(cardFilter ?? {})}`;

  // Loading state management
  // Determines when to show loading spinner vs content
  // Uses a custom hook that tracks multiple loading states and prevents flickering
  const { isInitialLoad } = usePageLoadingState({
    loadingStates: [decksLoading, cardsLoading, gameModesLoading],
    errorStates: [isDecksError, isCardsError, isGameModesError],
    hasData: () => Boolean(filteredDecks && filteredDecks.length > 0),
    // Reset dependency ensures loading state recalculates when any backend filter changes
    resetDependency: decksContextKey,
  });

  // Totals over every deck of the filter context, not only the returned top
  // decks. They are also the usage rate's denominator.
  const totalBattles = deckStats?.battleCount ?? 0;
  const totalWins = deckStats?.wins ?? 0;
  const totalDecks = deckStats?.deckCount ?? 0;
  // The backend returns at most its DECK_STATS_LIMIT top decks
  const hiddenDecks = totalDecks - filteredDecks.length;
  // Why only these decks are shown, for the hint above the list
  const capHint = (() => {
    const shown = formatNumber(filteredDecks.length);
    const total = formatNumber(totalDecks);
    const minimum =
      deckSort.minBattles > 1 ? ` with ${deckSort.minBattles}+ battles` : "";
    if (showMatch) {
      return `Showing ${shown} of ${total} decks, ranked by matched cards (most first).`;
    }
    // The option the user picked, also usage rate, which is sent as battles
    const names: Record<keyof DeckSortFields, string> = {
      battleCount: "battle count",
      wins: "wins",
      winRate: "win rate",
      usageRate: "usage rate",
      lastSeen: "last seen",
    };
    const first =
      deckSort.sortBy === "lastSeen"
        ? sortAscending
          ? "oldest first"
          : "newest first"
        : sortAscending
          ? "lowest first"
          : "highest first";
    const ranking = `${minimum}, ranked by ${names[selectedSortOption]} (${first})`;
    // The shown decks still have the previous order and counts
    if (sortPending) return `Loading the top decks${ranking}...`;
    return `Showing ${shown} of ${total} decks${ranking}.`;
  })();

  // The filters need the cards and game modes. A deck error shows in the
  // results instead, so a filter or sort the backend rejects can be changed.
  if (isCardsError || isGameModesError) {
    return (
      <PlayerError
        sources={[
          { label: "cards", failed: isCardsError, retry: refetchCards },
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
    <div className="decks-page">
      <div className="decks-content">
        {/* Loading State - Shows during initial load, cards loading, decks loading, or game mode loading */}
        {/* The loading spinner prevents users from seeing incomplete data during the initialization process */}
        {(isInitialLoad ||
          decksLoading ||
          cardsLoading ||
          gameModesLoading) && (
          <div>
            <CircularProgress className="decks-loading-spinner" />
            <p>Loading decks...</p>
          </div>
        )}
        {/* Loaded State - Show decks when all data is available and no errors occurred */}
        {!isInitialLoad && (
          <>
            {/* FilterContainer component */}
            <FilterContainer
              gameModes={gameModes || {}}
              cards={cards || []}
              gameModesLoading={gameModesLoading}
              onFiltersApply={handleFiltersApply}
              showCardFilter={true}
              appliedFilters={appliedFilters}
              initialFilters={getCurrentFilterState()}
            />

            <SortByContainer<DeckSortFields>
              options={sortOptions}
              selectedOption={selectedSortOption}
              ascending={sortAscending}
              // Only enable deck sorting in Include mode (when cards are filtered/selected with include mode)
              disableSort={showMatch}
              onSelectedOptionChange={handleSortChange}
            >
              <div className="sort-by-container-group">
                <h2 className="sort-by-container-header">Min. battles</h2>
                <div className="sort-by-container-content">
                  <select
                    className="sort-by-container-select decks-min-battles-select"
                    value={minBattles}
                    onChange={(e) => setMinBattles(Number(e.target.value))}
                    aria-label="Minimum battles per deck"
                  >
                    {MIN_BATTLE_OPTIONS.map((n) => (
                      <option key={n} value={n}>
                        {n === 1 ? "Any" : `${n}+`}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </SortByContainer>

            {isDecksError && (
              <PlayerError
                // A new filter or sort starts with fresh retry attempts
                key={`${decksContextKey}-${JSON.stringify(deckSort)}`}
                compact
                title="Couldn't load the decks"
                message="The decks didn't load. Try again, or change the filters or sort."
                sources={[
                  { label: "decks", failed: true, retry: refetchDecks },
                ]}
              />
            )}

            {/* Show decks if there is any data to display */}
            {!isDecksError && filteredDecks && filteredDecks.length > 0 && (
              <div
                className={`decks-stats${sortPending ? " is-pending" : ""}`}
                aria-busy={sortPending}
              >
                <h2>Overall Performance</h2>
                {/* The hint sits above the divider, with the totals it explains */}
                <div className="decks-general-stats-block">
                  <div className="decks-general-stats">
                    <StatCard
                      label={pluralize(totalBattles, "Battle", "Battles")}
                      value={totalBattles}
                    />
                    <StatCard
                      label={pluralize(totalDecks, "Deck", "Decks")}
                      value={totalDecks}
                    />
                    <StatCard
                      label={pluralize(totalWins, "Win", "Wins")}
                      value={totalWins}
                    />
                    <StatCard
                      label="Win Rate"
                      value={`${round((totalWins / totalBattles) * 100, 1)}%`}
                    />
                  </div>
                  {hiddenDecks > 0 && (
                    <p className="decks-cap-hint">
                      {capHint} Tighten the filters (time range, game modes,
                      cards) to see other decks.
                    </p>
                  )}
                </div>
                <VirtualDeckList
                  decks={filteredDecks}
                  cards={cards ?? []}
                  totalBattles={totalBattles}
                  matchedCards={appliedFilters.cards}
                  matchedSupportIds={appliedFilters.supportIds}
                  scrollingToTopRef={scrollingToTopRef}
                  showMatch={showMatch}
                />
              </div>
            )}
            <ScrollToTopButton onScrollStart={handleScrollToTopStart} />

            {/* Show message when no decks are found and not still loading */}
            {(!filteredDecks || filteredDecks.length === 0) &&
              !isDecksError &&
              !decksLoading &&
              !gameModesLoading &&
              !cardsLoading && (
                <div className="no-decks-message">
                  <p>No decks found with the current filters applied</p>
                </div>
              )}
          </>
        )}
      </div>
    </div>
  );
}
