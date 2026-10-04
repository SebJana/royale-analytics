import api from "./axios";
import type { CardMeta, CardsResponse } from "../../types/cards";

/**
 * Fetches the card list as one flat list: the regular cards, then the tower
 * troops, each tagged with its category. Lookups by id work the same for both.
 */
export async function fetchAllCards(): Promise<CardMeta[]> {
  const { data } = await api.get<CardsResponse>("/cards");
  return [
    ...data.items.map((card) => ({ ...card, category: "card" as const })),
    // Optional and cleaned up by the data scraper. Still guarded, so a
    // malformed field can never break every page that needs the card list.
    ...(Array.isArray(data.supportItems) ? data.supportItems : [])
      .filter((card) => card?.id != null && Boolean(card.name))
      .map((card) => ({ ...card, category: "support" as const })),
  ];
}
