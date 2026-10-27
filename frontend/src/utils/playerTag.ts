/**
 * Checks for one leading '#', 4-12 tag characters, and the Supercell alphabet.
 * Does NOT check if the tag exists.
 *
 * @param playerTag - The player tag starting with '#' (e.g., "#YYRJQY28")
 * @returns True if valid, false otherwise
 */
export function validatePlayerTagSyntax(playerTag: string): boolean {
  const ALPHABET = new Set("0289PYLQGRJCUV"); // Supercell-Tag-Alphabet

  // Trim whitespace
  const tag = playerTag.trim();

  if (!tag.startsWith("#") || tag.slice(1).includes("#")) {
    return false;
  }

  const core = tag.slice(1); // Part without the leading '#'
  // NOTE match the backend's current 4-12 limit. Update both validators if Clash
  // Royale starts (or already is) issuing shorter or longer tags.
  if (core.length < 4 || core.length > 12) {
    return false;
  }

  // Every char must be in the possible alphabet
  // Also minimizes injection risk, because special characters won't pass the check
  for (const ch of core) {
    if (!ALPHABET.has(ch)) {
      return false;
    }
  }

  return true;
}

/**
 * Turn typed text into a player tag if it can be one: "#" optional, any
 * case, whitespace ignored, and O read as 0 (the tag alphabet has no O).
 *
 * NOTE: Mirrors normalize_tag in backend/app/src/player_search/normalize.py.
 *
 * @param input - Text as typed, e.g. "yyrjqy28" or "# YYRJQY2O"
 * @returns The tag with a leading "#" (e.g. "#YYRJQY28"), or null
 */
export function normalizePlayerTag(input: string): string | null {
  const compact = input.normalize("NFKC").replace(/\s+/g, "");
  const tag = `#${compact.replace(/#/g, "").toUpperCase().replace(/O/g, "0")}`;
  return validatePlayerTagSyntax(tag) ? tag : null;
}

// Query parameter that opens the home page's add form with a tag filled in,
// and the id of that form's section.
export const ADD_PLAYER_PARAM = "add";
export const ADD_PLAYER_SECTION_ID = "add-player";

/**
 * Link to the home page's add form with a tag filled in, e.g. for an
 * opponent who is not tracked yet. Adding still takes a click there.
 *
 * @param playerTag - The tag to fill in, e.g. "#YYRJQY28"
 * @returns The path, e.g. "/?add=%23YYRJQY28#add-player"
 */
export function addPlayerPath(playerTag: string): string {
  const query = new URLSearchParams({ [ADD_PLAYER_PARAM]: playerTag });
  return `/?${query}#${ADD_PLAYER_SECTION_ID}`;
}
