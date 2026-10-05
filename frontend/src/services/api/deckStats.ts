import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import type {
  DeckCardFilter,
  DeckSort,
  DeckStats,
} from "../../types/deckStats";

/**
 * Fetches the player's decks of a date range, filtered on the backend.
 *
 * @param playerTag - Tag of a tracked player
 * @param startDate - First day, YYYY-MM-DD in the browser's timezone
 * @param endDate - Last day (inclusive), YYYY-MM-DD
 * @param gameModes - Internal mode names, omitted for all modes
 * @param cardFilter - Card and tower troop filter, omitted for all decks
 * @param sort - Order the backend sorts by before it caps the list
 * @returns The deck statistics
 * @throws Error for an invalid player tag, or the request error
 */
export async function fetchDeckStats(
  playerTag: string,
  startDate: string,
  endDate: string,
  gameModes?: string[],
  cardFilter?: DeckCardFilter,
  sort?: DeckSort,
): Promise<DeckStats> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  params.set("start_date", startDate);
  params.set("end_date", endDate);

  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  params.set("timezone", timeZone);

  // Append game modes if they exist as param
  if (gameModes?.length) {
    gameModes.forEach((mode) => params.append("game_modes", mode));
  }

  if (sort) {
    params.set("sort_by", sort.sortBy);
    params.set("sort_order", sort.sortOrder);
    if (sort.minBattles > 1) {
      params.set("min_battles", String(sort.minBattles));
    }
  }

  // Empty lists are left out, so no selection requests all decks
  if (cardFilter) {
    params.set("card_mode", cardFilter.mode);
    cardFilter.cards.forEach((key) => params.append("cards", key));
    cardFilter.excludeCards.forEach((key) =>
      params.append("exclude_cards", key),
    );
    cardFilter.supportIds.forEach((id) =>
      params.append("support_ids", String(id)),
    );
    cardFilter.excludeSupportIds.forEach((id) =>
      params.append("exclude_support_ids", String(id)),
    );
  }

  const url = `/players/${tag}/decks/stats?${params.toString()}`;

  const response = await api.get<DeckStats>(url);
  return response.data;
}
