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
    // The data scraper always stores the list, entries without an id or
    // name already removed
    ...data.supportItems.map((card) => ({
      ...card,
      category: "support" as const,
    })),
  ];
}
