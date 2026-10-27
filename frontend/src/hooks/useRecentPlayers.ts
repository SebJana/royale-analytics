import { useSyncExternalStore } from "react";
import {
  readPreference,
  subscribeConsent,
  writePreference,
} from "../utils/storage";
import { validatePlayerTagSyntax } from "../utils/playerTag";

const day = 24 * 60 * 60 * 1000;
const RECENT_PLAYERS_KEY = "recentPlayers";
// Enough to jump between the players someone follows. Longer lists scroll
// inside the dropdown.
// NOTE Match the retention text of recentPlayers in STORED_ITEMS.
const MAX_RECENT_PLAYERS = 10;
// A player not viewed for this long drops off the list, so the history does
// not grow older than it is useful.
const RECENT_PLAYER_MAX_AGE = 90 * day;

export type RecentPlayer = {
  tag: string;
  name: string;
  viewedAt: number;
};

const listeners = new Set<() => void>();
// Parsed once per stored string, so useSyncExternalStore gets a stable
// snapshot instead of a new array on every render.
let cachedRaw: string | null = null;
let cachedPlayers: RecentPlayer[] = [];

function isRecentPlayer(value: unknown): value is RecentPlayer {
  const player = value as RecentPlayer;
  return (
    typeof player?.tag === "string" &&
    validatePlayerTagSyntax(player.tag) &&
    typeof player.name === "string" &&
    typeof player.viewedAt === "number"
  );
}

function parse(raw: string | null): RecentPlayer[] {
  if (!raw) return [];
  try {
    const stored = JSON.parse(raw);
    if (stored?.v !== 1 || !Array.isArray(stored.players)) return [];
    const oldest = Date.now() - RECENT_PLAYER_MAX_AGE;
    return stored.players
      .filter(isRecentPlayer)
      .filter((player: RecentPlayer) => player.viewedAt >= oldest)
      .slice(0, MAX_RECENT_PLAYERS);
  } catch {
    return [];
  }
}

function getRecentPlayers(): RecentPlayer[] {
  const raw = readPreference(RECENT_PLAYERS_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedPlayers = parse(raw);
  }
  return cachedPlayers;
}

function saveRecentPlayers(players: RecentPlayer[]) {
  writePreference(RECENT_PLAYERS_KEY, JSON.stringify({ v: 1, players }));
  for (const listener of listeners) listener();
}

// A consent change moves the list between localStorage and sessionStorage,
// and another tab can change the localStorage copy.
function subscribe(listener: () => void) {
  listeners.add(listener);
  const unsubscribeConsent = subscribeConsent(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === RECENT_PLAYERS_KEY || event.key === null) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    unsubscribeConsent();
    window.removeEventListener("storage", onStorage);
  };
}

/**
 * Put a player first in the recently viewed list, replacing an older entry of
 * the same tag. Kept across visits only with preference consent.
 *
 * @param player - Tag (with "#") and name of the viewed player
 */
export function recordRecentPlayer(player: { tag: string; name: string }) {
  if (!validatePlayerTagSyntax(player.tag)) return;
  saveRecentPlayers(
    [
      { tag: player.tag, name: player.name, viewedAt: Date.now() },
      ...getRecentPlayers().filter((p) => p.tag !== player.tag),
    ].slice(0, MAX_RECENT_PLAYERS),
  );
}

/**
 * Remove one player from the recently viewed list.
 *
 * @param tag - Tag of the player, with "#"
 */
export function removeRecentPlayer(tag: string) {
  const players = getRecentPlayers();
  if (players.some((p) => p.tag === tag))
    saveRecentPlayers(players.filter((p) => p.tag !== tag));
}

/**
 * Empty the recently viewed list.
 */
export function clearRecentPlayers() {
  saveRecentPlayers([]);
}

/**
 * Recently viewed players, newest first.
 *
 * @returns At most MAX_RECENT_PLAYERS players, none older than 90 days
 */
export function useRecentPlayers(): RecentPlayer[] {
  return useSyncExternalStore(subscribe, getRecentPlayers);
}
