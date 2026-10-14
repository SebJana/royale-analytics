import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Autocomplete,
  TextField,
  ListItem,
  ListItemText,
  Paper,
  type PaperProps,
} from "@mui/material";
import {
  ArrowRight,
  CircleAlert,
  Flame,
  History,
  Search,
  SearchX,
  type LucideIcon,
} from "lucide-react";
import { usePlayerSearch } from "../../hooks/usePlayerSearch";
import {
  clearRecentPlayers,
  removeRecentPlayer,
  useRecentPlayers,
} from "../../hooks/useRecentPlayers";
import { useExplorePlayers } from "../../hooks/useExplorePlayers";
import { useStorageConsent } from "../../hooks/useStorageConsent";
import { StorageSettingsContext } from "../../contexts/StorageSettingsContext";
import type { PlayerSearchResult } from "../../types/players";
import { formatCompactNumber } from "../../utils/number";
import { normalizePlayerTag } from "../../utils/playerTag";
import "./playerSearch.css";
import { AppTooltip } from "../tooltip/tooltip";
import { Sticker } from "../sticker/sticker";
import { CloseButton } from "../closeButton/closeButton";

type Player = { tag: string; name: string };
// A typed tag the search did not return. Offered as an option, so a player
// can be opened by tag even when search misses them or is unavailable.
type TypedTagOption = { tag: string; name: ""; match: "typedTag" };
// Players the visitor opened before, to jump back without searching.
type RecentOption = { tag: string; name: string; match: "recent" };
// The most viewed players, to discover players without knowing a name.
type PopularOption = {
  tag: string;
  name: string;
  match: "popular";
  rank: number;
  views30d: number;
};
// The row that loads more popular players. A row rather than a button, so
// the arrow keys reach it like the players above it.
type MoreOption = { tag: ""; name: ""; match: "more" };
type SearchOption =
  | PlayerSearchResult
  | TypedTagOption
  | RecentOption
  | PopularOption
  | MoreOption;
// hidden marks the selected player in a list it does not belong to: MUI needs
// the value among the options, but it is neither shown nor reachable there.
type ListOption = SearchOption & { hidden?: boolean };

// The lists the dropdown switches between. A new one (e.g. top players of the
// ladder) needs an entry here and its options in PlayerSearch.
type ListId = "search" | "recent" | "popular";
const LISTS: { id: ListId; label: string; icon: LucideIcon }[] = [
  { id: "search", label: "Search", icon: Search },
  { id: "recent", label: "Recent", icon: History },
  { id: "popular", label: "Popular", icon: Flame },
];

// Room the dropdown needs below the input: the tabs, a header and about five
// rows. Less room scrolls the page, but never the input closer to the top of
// the screen than the margin.
const DROPDOWN_ROOM_PX = 420;
const DROPDOWN_TOP_MARGIN_PX = 16;

// The dropdown paper reads this instead of taking props, so it can stay a
// stable component and MUI does not remount the list on every render.
const DropdownContext = createContext<{
  list: ListId;
  onSelectList: (list: ListId) => void;
  header: ReactNode;
  empty: ReactNode;
  footer: ReactNode;
}>({
  list: "search",
  onSelectList: () => {},
  header: null,
  empty: null,
  footer: null,
});

/**
 * What an empty list says: an optional icon, a short title and a hint. Laid
 * out like a list row (same edge and height as the players), but muted and
 * inert, so it reads as a note about the list, not as a field to type into.
 */
function EmptyState({
  icon: Icon,
  title,
  hint,
}: Readonly<{ icon?: LucideIcon; title: string; hint: string }>) {
  return (
    <div className="player-search-empty-state">
      {Icon && <Icon aria-hidden="true" />}
      <span className="player-search-empty-text">
        <span className="player-search-empty-title">{title}</span>
        <span className="player-search-empty-hint">{hint}</span>
      </span>
    </div>
  );
}

function playerLabel(player: Player): string {
  return player.name ? `${player.name} (${player.tag})` : player.tag;
}

function optionLabel(option: SearchOption): string {
  return option.match === "more" ? "Show more" : playerLabel(option);
}

function ResultsPaper({
  children,
  className,
  onMouseDown,
  ...props
}: PaperProps) {
  const { list, onSelectList, header, empty, footer } =
    useContext(DropdownContext);
  return (
    <Paper
      {...props}
      // MUI portals the dropdown out of .player-search; this class styles it.
      className={`${className ?? ""} player-search-paper`}
      // A click on a tab, header or button keeps the input focused, so the
      // list stays open. Option clicks still select.
      onMouseDown={(event) => {
        event.preventDefault();
        onMouseDown?.(event);
      }}
    >
      <div
        className="player-search-tabs"
        role="tablist"
        aria-label="Lists, switch with the left and right arrow keys"
      >
        {LISTS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={list === id}
            className={`player-search-tab${list === id ? " is-active" : ""}`}
            onClick={() => onSelectList(id)}
          >
            <Icon aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      {header && <div className="player-search-list-header">{header}</div>}
      {empty}
      {children}
      {footer}
    </Paper>
  );
}

export function PlayerSearch({
  onSelectPlayer,
  onInput,
}: Readonly<{
  onSelectPlayer?: (player: Player | null) => void;
  /** Called when the user types into or clears the search. */
  onInput?: () => void;
}>) {
  const [selected, setSelected] = useState<SearchOption | null>(null);
  // Only typed text is searched. Selecting a player fills the input with its
  // label, which is not a query.
  const [searchText, setSearchText] = useState("");
  const [list, setList] = useState<ListId>("search");
  const { data, isFetching, isError } = usePlayerSearch(searchText);
  const recentPlayers = useRecentPlayers();
  // Only fetched once the list is shown; most visitors search instead.
  const popular = useExplorePlayers(list === "popular");
  // The row the arrow keys are on, so Enter on "Show more" loads instead of
  // selecting.
  const highlighted = useRef<SearchOption | null>(null);
  const consent = useStorageConsent();
  const openStorageSettings = useContext(StorageSettingsContext);
  const containerRef = useRef<HTMLDivElement>(null);

  // The dropdown always opens below the input (see slotProps.popper). With
  // little room left below, the page scrolls the input up so the list fits
  // instead of running off the screen.
  const makeRoomBelow = () => {
    const input = containerRef.current;
    if (!input) return;
    const { top, bottom } = input.getBoundingClientRect();
    const missing = DROPDOWN_ROOM_PX - (window.innerHeight - bottom);
    if (missing > 0)
      window.scrollBy({
        top: Math.min(missing, top - DROPDOWN_TOP_MARGIN_PX),
        behavior: "smooth",
      });
  };

  // Removing the selected player from the history drops the selection too.
  // Otherwise the selected-value fallback below would put it straight back
  // into the list.
  const forgetRecent = (tag?: string) => {
    if (selected?.match === "recent" && (!tag || selected.tag === tag)) {
      setSelected(null);
      onSelectPlayer?.(null);
    }
    if (tag) removeRecentPlayer(tag);
    else clearRecentPlayers();
  };

  const hasQuery = searchText.trim() !== "";
  // Without a query, the visitor's own players come first, or the popular
  // ones for a visitor without any.
  const emptyInputList: ListId =
    recentPlayers.length > 0 ? "recent" : "popular";
  const results = hasQuery ? (data?.players ?? []) : [];

  let options: ListOption[] = [];
  let header: ReactNode = null;
  let footer: ReactNode = null;
  let loading = false;
  let emptyState: ReactNode = null;

  if (list === "search") {
    options = results;
    const typedTag = normalizePlayerTag(searchText);
    // If search returned this tag, the real result is already pinned first.
    if (typedTag && !results.some((p) => p.tag === typedTag)) {
      const typed: TypedTagOption = {
        tag: typedTag,
        name: "",
        match: "typedTag",
      };
      // With "#" the tag is clearly meant; without, names stay on top.
      options = searchText.trim().startsWith("#")
        ? [typed, ...results]
        : [...results, typed];
    }
    loading = isFetching;
    if (!hasQuery)
      emptyState = (
        <EmptyState
          title="Start typing above"
          hint="A player name, or a tag like #YYRJQY28"
        />
      );
    else if (isError)
      emptyState = (
        <EmptyState
          icon={CircleAlert}
          title="Search is unavailable right now"
          hint="A full tag still opens the player"
        />
      );
    else
      emptyState = (
        <EmptyState
          icon={SearchX}
          title="No tracked players found"
          hint="Check the spelling, or add the player below"
        />
      );
    if (hasQuery && data?.hasMore)
      footer = (
        <div className="player-search-more-hint">
          Showing the first {results.length} matches. Type more to narrow it
          down. Names aren't unique, only a tag (e.g. #YYRJQY28) is sure to find
          a specific account.
        </div>
      );
  } else if (list === "recent") {
    options = recentPlayers.map(({ tag, name }) => ({
      tag,
      name,
      match: "recent",
    }));
    emptyState = (
      <EmptyState
        icon={History}
        title="No recently viewed players"
        hint="Players you open show up here"
      />
    );
    if (options.length > 0)
      header = (
        <>
          <span>Players you opened</span>
          <button
            type="button"
            className="player-search-text-button"
            onClick={() => forgetRecent()}
          >
            Clear
          </button>
        </>
      );
    if (consent?.preferences !== true)
      footer = (
        <div className="player-search-more-hint">
          Kept until this tab closes.{" "}
          <button
            type="button"
            className="player-search-text-button"
            onClick={openStorageSettings}
          >
            Remember across visits?
          </button>
        </div>
      );
  } else {
    // A ranking refresh between two pages can move a player into the next
    // one; it is shown once, at its first place.
    const seen = new Set<string>();
    options = (popular.data?.pages ?? []).flatMap((page) =>
      page.players.flatMap((player): PopularOption[] => {
        if (seen.has(player.tag)) return [];
        seen.add(player.tag);
        return [
          {
            tag: player.tag,
            name: player.name ?? "",
            match: "popular",
            rank: player.rank,
            views30d: player.views30d,
          },
        ];
      }),
    );
    loading = popular.isPending;
    emptyState = popular.isError ? (
      <EmptyState
        icon={CircleAlert}
        title="Popular players are unavailable right now"
        hint="Search still finds every tracked player"
      />
    ) : (
      <EmptyState
        icon={Flame}
        title="No popular players yet"
        hint="Players show up here once they get visitors"
      />
    );
    // NOTE: "30 days" matches VIEWS_WINDOW_DAYS in the data scraper settings.
    if (options.length > 0) header = <span>Visitors in the last 30 days</span>;
    if (popular.hasNextPage)
      options = [...options, { tag: "", name: "", match: "more" }];
  }
  // MUI expects the selected value among the options. Another list's player
  // is kept out of sight there, so it neither shows up above rank 1 nor
  // hides the list's empty message.
  let empty: ReactNode = null;
  if (selected && !options.some((p) => p.tag === selected.tag)) {
    if (options.length === 0) empty = emptyState;
    options = [...options, { ...selected, hidden: true }];
  }

  const loadMorePopular = () => {
    if (!popular.isFetchingNextPage) void popular.fetchNextPage();
  };

  return (
    <div className="player-search" ref={containerRef}>
      <DropdownContext.Provider
        value={{ list, onSelectList: setList, header, empty, footer }}
      >
        <Autocomplete
          options={options}
          value={selected}
          // The server already ranked and filtered the results.
          filterOptions={(x) => x}
          // Enter picks the first option, the best match.
          autoHighlight
          // Shows the recent or most viewed players before anything is typed.
          openOnFocus
          onOpen={() => {
            setList(hasQuery ? "search" : emptyInputList);
            makeRoomBelow();
          }}
          getOptionLabel={optionLabel}
          getOptionDisabled={(option) => option.hidden === true}
          onHighlightChange={(_, option) => {
            highlighted.current = option;
          }}
          onKeyDown={(event) => {
            const input = event.target as HTMLInputElement;
            // Without text the caret has nowhere to go, so the arrows switch
            // between the lists instead.
            if (
              (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
              input.value === ""
            ) {
              const index = LISTS.findIndex(({ id }) => id === list);
              const step = event.key === "ArrowLeft" ? -1 : 1;
              setList(LISTS[(index + step + LISTS.length) % LISTS.length].id);
              event.preventDefault();
            } else if (
              event.key === "Enter" &&
              highlighted.current?.match === "more"
            ) {
              // Loading is not a selection, so MUI must not close the list.
              (
                event as typeof event & { defaultMuiPrevented: boolean }
              ).defaultMuiPrevented = true;
              event.preventDefault();
              loadMorePopular();
            }
          }}
          // The same player can be in several lists, so the list is part of
          // the key.
          getOptionKey={(option) => `${option.match}:${option.tag}`}
          isOptionEqualToValue={(option, value) => option.tag === value.tag}
          loading={loading}
          noOptionsText={emptyState}
          slots={{ paper: ResultsPaper }}
          slotProps={{
            popper: {
              placement: "bottom-start",
              // Always below the input. Popper would otherwise flip it above
              // when space runs short, and lists of different heights made it
              // jump between both sides while switching tabs.
              modifiers: [{ name: "flip", enabled: false }],
            },
          }}
          // Leaving the input keeps the typed text, so coming back shows the
          // query together with its results.
          clearOnBlur={false}
          onInputChange={(_, value, reason) => {
            if (reason === "input" || reason === "clear") {
              setSearchText(value);
              setList(reason === "input" ? "search" : emptyInputList);
              onInput?.();
            } else if (reason !== "blur") {
              // Selecting or resetting fills the input with a player's label
              // (or nothing), which is not a query; results of the old one
              // would no longer match what is visible.
              setSearchText("");
            }
          }}
          renderOption={(props, option) => {
            const { key, className, ...optionProps } = props;
            if (option.hidden) return null;
            if (option.match === "more") {
              return (
                <ListItem
                  key={key}
                  {...optionProps}
                  className={`${className ?? ""} player-search-show-more`}
                  // Loads instead of selecting, so the list stays open
                  onClick={loadMorePopular}
                  disableGutters
                >
                  {popular.isFetchingNextPage ? "Loading…" : "Show more"}
                </ListItem>
              );
            }
            if (option.match === "typedTag") {
              // Styled as an action, not a result: it opens a page by tag
              // without the search having found the player.
              return (
                <ListItem
                  key={key}
                  {...optionProps}
                  className={`${className ?? ""} player-search-typed-tag`}
                  disableGutters
                >
                  <ListItemText
                    primary={
                      <span className="player-search-typed-tag-action">
                        Open {option.tag}
                        <ArrowRight aria-hidden="true" />
                      </span>
                    }
                    secondary="Not a search result, opens the player by tag"
                  />
                </ListItem>
              );
            }
            if (option.match === "recent") {
              return (
                <ListItem
                  key={key}
                  {...optionProps}
                  className={`${className ?? ""} player-search-recent`}
                  disableGutters
                >
                  <ListItemText
                    primary={option.name || option.tag}
                    secondary={option.tag}
                  />
                  <AppTooltip title="Remove">
                    <CloseButton
                      className="player-search-recent-remove"
                      aria-label={`Remove ${playerLabel(option)} from recently viewed`}
                      onClick={(event) => {
                        // Removing must not also select the player.
                        event.stopPropagation();
                        forgetRecent(option.tag);
                      }}
                    />
                  </AppTooltip>
                </ListItem>
              );
            }
            if (option.match === "popular") {
              return (
                <ListItem
                  key={key}
                  {...optionProps}
                  className={`${className ?? ""} player-search-popular`}
                  disableGutters
                >
                  {option.rank <= 3 ? (
                    <Sticker
                      tone="gold"
                      size="sm"
                      className="player-search-popular-rank"
                    >
                      #{option.rank}
                    </Sticker>
                  ) : (
                    <span className="player-search-popular-rank">
                      #{option.rank}
                    </span>
                  )}
                  <ListItemText
                    primary={option.name || option.tag}
                    secondary={option.tag}
                  />
                  <span className="player-search-popular-views">
                    {formatCompactNumber(option.views30d)}
                    <span className="player-search-popular-views-label">
                      {" "}
                      views
                    </span>
                  </span>
                </ListItem>
              );
            }
            return (
              <ListItem
                key={key}
                {...optionProps}
                className={className}
                disableGutters
              >
                <ListItemText
                  primary={option.name || option.tag}
                  secondary={option.tag}
                />
              </ListItem>
            );
          }}
          renderInput={(params) => (
            <TextField {...params} label="Search players…" size="small" />
          )}
          onChange={(_, player) => {
            setSelected(player);
            onSelectPlayer?.(player);
          }}
        />
      </DropdownContext.Provider>
    </div>
  );
}
