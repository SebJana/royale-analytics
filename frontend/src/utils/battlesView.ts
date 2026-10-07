// The battles page's filter and scroll position, so switching to another page
// of the same player and back lands there instead of at the newest battle.
// Kept in sessionStorage so a reload keeps it too. Leaving the player drops it
// (pages/player/layout.tsx), so a later visit starts at the newest battle.
export type BattlesView = {
  beforeDate: string;
  appliedBeforeDate?: string;
  scrollY: number;
  // Loaded pages when the position was saved. The position only fits while
  // the query cache still holds at least that many.
  pageCount: number;
};

// NOTE Listed in STORED_ITEMS (utils/storage.ts) as a necessary item.
function battlesViewKey(playerTag: string) {
  return `battlesView:${playerTag}`;
}

/**
 * Read the saved battles view of a player.
 *
 * @param playerTag - Tag of the player, with "#"
 * @returns The saved view, or null if none is saved or storage is blocked
 */
export function readBattlesView(playerTag: string): BattlesView | null {
  try {
    const raw = sessionStorage.getItem(battlesViewKey(playerTag));
    return raw ? (JSON.parse(raw) as BattlesView) : null;
  } catch {
    return null;
  }
}

/**
 * Save the battles view of a player.
 *
 * @param playerTag - Tag of the player, with "#"
 * @param view - Filter, scroll position and loaded page count
 */
export function writeBattlesView(playerTag: string, view: BattlesView) {
  try {
    sessionStorage.setItem(battlesViewKey(playerTag), JSON.stringify(view));
  } catch {
    // Blocked storage only costs the restored position
  }
}

/**
 * Drop the saved battles view of a player.
 *
 * @param playerTag - Tag of the player, with "#"
 */
export function clearBattlesView(playerTag: string) {
  try {
    sessionStorage.removeItem(battlesViewKey(playerTag));
  } catch {
    // Blocked storage holds nothing to drop
  }
}
