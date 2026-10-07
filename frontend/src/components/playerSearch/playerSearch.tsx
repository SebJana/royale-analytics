import { createContext, useContext, useState } from "react";
import {
  Autocomplete,
  TextField,
  ListItem,
  ListItemText,
  Paper,
  type PaperProps,
} from "@mui/material";
import { ArrowRight, X } from "lucide-react";
import { usePlayerSearch } from "../../hooks/usePlayerSearch";
import {
  clearRecentPlayers,
  removeRecentPlayer,
  useRecentPlayers,
} from "../../hooks/useRecentPlayers";
import { useStorageConsent } from "../../hooks/useStorageConsent";
import { StorageSettingsContext } from "../../contexts/StorageSettingsContext";
import type { PlayerSearchResult } from "../../types/players";
import { normalizePlayerTag } from "../../utils/playerTag";
import "./playerSearch.css";

type Player = { tag: string; name: string };
// A typed tag the search did not return. Offered as an option, so a player
// can be opened by tag even when search misses them or is unavailable.
type TypedTagOption = { tag: string; name: ""; match: "typedTag" };
// Offered while the input is empty, so users can jump back without searching.
type RecentOption = { tag: string; name: string; match: "recent" };
type SearchOption = PlayerSearchResult | TypedTagOption | RecentOption;

type RecentHeader = {
  // False without preference consent: the list ends with the tab.
  persistent: boolean;
  onOpenSettings: () => void;
  onClear: () => void;
};

// The dropdown paper reads this instead of taking props, so it can stay a
// stable component and MUI does not remount the list on every render.
const DropdownContext = createContext<{
  shownCount: number | null;
  recent: RecentHeader | null;
}>({ shownCount: null, recent: null });

function playerLabel(player: Player): string {
  return player.name ? `${player.name} (${player.tag})` : player.tag;
}

function ResultsPaper({
  children,
  className,
  onMouseDown,
  ...props
}: PaperProps) {
  const { shownCount, recent } = useContext(DropdownContext);
  return (
    <Paper
      {...props}
      // MUI portals the dropdown out of .player-search; this class styles it.
      className={`${className ?? ""} player-search-paper`}
      // A click on the hint or padding keeps the input focused, so the list
      // stays open. Option clicks still select.
      onMouseDown={(event) => {
        event.preventDefault();
        onMouseDown?.(event);
      }}
    >
      {recent && (
        <div className="player-search-recent-header">
          <span className="player-search-recent-title">Recently viewed</span>
          <button
            type="button"
            className="player-search-text-button"
            onClick={recent.onClear}
          >
            Clear
          </button>
        </div>
      )}
      {children}
      {recent && !recent.persistent && (
        <div className="player-search-more-hint">
          Kept until this tab closes.{" "}
          <button
            type="button"
            className="player-search-text-button"
            onClick={recent.onOpenSettings}
          >
            Remember across visits?
          </button>
        </div>
      )}
      {shownCount !== null && (
        <div className="player-search-more-hint">
          Showing the first {shownCount} matches. Type more to narrow it down.
          Names aren't unique, only a tag (e.g. #YYRJQY28) is sure to find a
          specific account.
        </div>
      )}
    </Paper>
  );
}

export function PlayerSearch({
  onSelectPlayer,
}: Readonly<{
  onSelectPlayer?: (player: Player | null) => void;
}>) {
  const [selected, setSelected] = useState<SearchOption | null>(null);
  // Only typed text is searched. Selecting a player fills the input with its
  // label, which is not a query.
  const [searchText, setSearchText] = useState("");
  const { data, isFetching, isError } = usePlayerSearch(searchText);
  const recentPlayers = useRecentPlayers();
  const consent = useStorageConsent();
  const openStorageSettings = useContext(StorageSettingsContext);

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
  const results = hasQuery ? (data?.players ?? []) : [];
  const shownCount = hasQuery && data?.hasMore ? results.length : null;
  const showRecent = !hasQuery && recentPlayers.length > 0;
  const recent: RecentHeader | null = showRecent
    ? {
        persistent: consent?.preferences === true,
        onOpenSettings: openStorageSettings,
        onClear: () => forgetRecent(),
      }
    : null;

  const typedTag = normalizePlayerTag(searchText);
  let options: SearchOption[] = showRecent
    ? recentPlayers.map(({ tag, name }) => ({ tag, name, match: "recent" }))
    : results;
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
  // MUI expects the selected value among the options.
  if (selected && !options.some((p) => p.tag === selected.tag)) {
    options = [selected, ...options];
  }

  let noOptionsText = "No tracked players found";
  if (!searchText.trim()) noOptionsText = "Type a player name or tag";
  else if (isError)
    noOptionsText = "Search is unavailable right now. A full tag still works.";

  return (
    <div className="player-search">
      <DropdownContext.Provider value={{ shownCount, recent }}>
        <Autocomplete
          options={options}
          value={selected}
          // The server already ranked and filtered the results.
          filterOptions={(x) => x}
          // Enter picks the first option, the best match.
          autoHighlight
          // Shows the recently viewed players before anything is typed.
          openOnFocus
          getOptionLabel={playerLabel}
          isOptionEqualToValue={(option, value) => option.tag === value.tag}
          loading={isFetching}
          noOptionsText={noOptionsText}
          slots={{ paper: ResultsPaper }}
          onInputChange={(_, value, reason) => {
            if (reason === "input" || reason === "clear") setSearchText(value);
          }}
          renderOption={(props, option) => {
            const { key, className, ...optionProps } = props;
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
                  <button
                    type="button"
                    className="player-search-recent-remove"
                    aria-label={`Remove ${playerLabel(option)} from recently viewed`}
                    title="Remove"
                    onClick={(event) => {
                      // Removing must not also select the player.
                      event.stopPropagation();
                      forgetRecent(option.tag);
                    }}
                  >
                    <X aria-hidden="true" />
                  </button>
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
