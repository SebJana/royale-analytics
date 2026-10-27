import type { GameModeQuery } from "../types/gameModes";

/**
 * Turns the selected modes into the shorter of the two equivalent filters.
 *
 * A selection of more than half of the available modes is sent as the modes
 * to leave out, so "all but one" is one name instead of every other mode.
 * That keeps the request URL short however many modes the game adds. An
 * exclude filter also keeps modes the available list does not hold yet,
 * which is what a selection of nearly everything means. Selecting every
 * available mode omits the filter, so the server uses the same cache entry as
 * an unfiltered request. The UI keeps the explicit selection either way, so
 * individual modes can still be removed.
 *
 * The modes are sorted, so the same selection in any click order is the same
 * query and the same server cache entry.
 *
 * @param selected - Internal names of the selected modes, empty for all
 * @param available - The mode catalogue, keyed by internal name
 * @returns The modes to send, sorted, and whether they are excluded
 */
export function gameModesForQuery(
  selected: string[],
  available: Record<string, string> | undefined,
): GameModeQuery {
  const availableNames = Object.keys(available ?? {});
  const selectedNames = new Set(selected);
  const sortedSelected = [...selectedNames].sort();
  // Without the catalogue the unselected modes are unknown, and an empty
  // exclude list would turn the selection into all modes.
  if (availableNames.length === 0 || selectedNames.size === 0) {
    return { modes: sortedSelected, exclude: false };
  }
  const unselected = availableNames
    .filter((name) => !selectedNames.has(name))
    .sort();
  // Every available mode: no filter, the same query as an empty selection
  if (unselected.length === 0) {
    return { modes: [], exclude: false };
  }
  // Selected modes the catalogue does not list are kept by an exclude filter
  // too, since only the unselected catalogue modes are left out.
  if (unselected.length < selectedNames.size) {
    return { modes: unselected, exclude: true };
  }
  return { modes: sortedSelected, exclude: false };
}

/**
 * Stable string form of a game mode filter, for keys and dependency lists.
 *
 * @param query - The filter, null while the modes are not initialized
 * @returns The same string for the same filter
 */
export function gameModeQueryKey(query: GameModeQuery | null): string {
  if (!query) {
    return "";
  }
  return `${query.exclude ? "exclude" : "include"}:${query.modes.join("|")}`;
}

/**
 * Builds a mapping from internal game mode names (as provided by the API/DB)
 * to user-friendly display names.
 *
 * @param gameModes - A record of internal game mode names as keys, with any lastSeen timestamp as value
 * @returns A Map where the key is the internal name and the value is the display name.
 *
 * @example
 * const gameModes = {
 *   Ranked1v1_NewArena: "2025-09-19T18:32:12.746000",
 *   CaptureTheEgg_Friendly: "2025-09-19T18:32:12.746000"
 * };
 *
 */
export function internalNamesToDisplayNames(
  gameModes: Record<string, string>,
): Map<string, string> {
  const internalAndDisplayNames = new Map<string, string>();

  for (const internalName of Object.keys(gameModes)) {
    const displayName = mapInternalNameToDisplayName(internalName);
    // e.g ("Ranked1v1_NewArena", "Ranked 1v1")
    // e.g ("Ranked1v1_NewArena2", "Ranked 1v1")
    internalAndDisplayNames.set(internalName, displayName);
  }

  return internalAndDisplayNames;
}

/**
 * Maps an internal game mode name into a user-friendly display name.
 *
 * Handles special cases like Ranked and Clan Wars modes ...
 * and falls back to a generic transformation (underscores → spaces,
 * split camel case, collapse spaces, and trim) for non-mapped game modes.
 *
 * @param internalName - The raw internal game mode name from the API/DB.
 * @returns A cleaned, human-friendly display name string.
 *
 * @example
 * mapInternalNameToDisplayName("Ranked1v1_NewArena");
 * // "Ranked 1v1"
 */
export function mapInternalNameToDisplayName(internalName: string): string {
  const splitName = internalName
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();

  // Friendly modes use their split raw names instead of one shared "Friendly" label.
  if (internalName.includes("Friendly")) {
    return splitName;
  }
  // TODO: Consider showing split raw names for the other grouped modes too.
  // Some names are very close together, so their labels need to stay distinguishable.
  if (internalName.startsWith("Ranked1v1_")) {
    return "Ranked 1v1";
  }
  if (internalName.startsWith("CW_")) {
    return "Clan Wars";
  }
  if (internalName.startsWith("ClanWar_BoatBattle")) {
    return "Clan Wars Boat Battle";
  }
  if (internalName.startsWith("Challenge_AllCards_EventDeck_NoSet")) {
    return "CRL 20-Win";
  }
  // Add new mappings here

  // Fallback: show the split raw name for other unmapped modes.
  return splitName;
}

/**
 * Extracts all internal game mode names from a map of internal → display names.
 *
 * @param internalAndDisplay - A Map where the key is the internal name
 *                            and the value is the display name.
 * @returns A list of all unique internal names (keys) from the map.
 */
export function internalDisplayMapToInternalNamesList(
  internalAndDisplay: Map<string, string>,
): string[] {
  const internalNames = new Set<string>(); // Use set to ensure unique values

  for (const internal of internalAndDisplay.keys()) {
    internalNames.add(internal);
  }

  return Array.from(internalNames).sort((a, b) => a.localeCompare(b)); // Return as list and sort ascending
}

/**
 * Extracts all display game mode names from a map of internal → display names.
 *
 * @param internalAndDisplay - A Map where the key is the internal name
 *                            and the value is the display name.
 * @returns A list of all unique display names (values) from the map.
 */
export function internalDisplayMapToDisplayNamesList(
  internalAndDisplay: Map<string, string>,
): string[] {
  const displayNames = new Set<string>(); // Use set to ensure unique values

  for (const display of internalAndDisplay.values()) {
    displayNames.add(display);
  }

  return Array.from(displayNames).sort((a, b) => a.localeCompare(b)); // Return as list and sort ascending
}
