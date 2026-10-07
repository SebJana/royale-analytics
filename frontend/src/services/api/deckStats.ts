import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import { setTimeRangeParams } from "./seasons";
import { setGameModeParams } from "./gameModes";
import type {
  DeckCardFilter,
  DeckSort,
  DeckStats,
} from "../../types/deckStats";
import type { TimeRange } from "../../types/seasons";
import type { GameModeQuery } from "../../types/gameModes";

/**
 * Fetches the player's decks of a timespan, filtered on the backend.
 *
 * @param playerTag - Tag of a tracked player
 * @param range - Season, or first and last day in the browser's timezone
 * @param gameModes - Modes to keep or to leave out, omitted for all modes
 * @param cardFilter - Card and tower troop filter, omitted for all decks
 * @param sort - Order the backend sorts by before it caps the list
 * @returns The deck statistics
 * @throws Error for an invalid player tag, or the request error
 */
export async function fetchDeckStats(
  playerTag: string,
  range: TimeRange,
  gameModes?: GameModeQuery,
  cardFilter?: DeckCardFilter,
  sort?: DeckSort,
): Promise<DeckStats> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  setTimeRangeParams(params, range);

  setGameModeParams(params, gameModes);

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
