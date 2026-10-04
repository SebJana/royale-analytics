import type { Card, CardMeta } from "../types/cards";

export function getCardName(cardID: number, cards: CardMeta[]): string {
  return cards.find((c) => c.id === cardID)?.name ?? `#${cardID}`;
}

// Variants by the evolutionLevel of a played card, with the iconUrls key of
// their art. A battle card is always one variant; the card list's
// maxEvolutionLevel 3 means a card has both, it is never a played level.
// NOTE: A level missing here (a variant added after this table) still renders,
// labelled "Variant <level>" with a placeholder, never as the regular card.
// Add it here and in createCardList (cardFilter.tsx).
const CARD_VARIANTS: Record<number, { label: string; iconKey: string }> = {
  0: { label: "", iconKey: "medium" },
  1: { label: "Evolution", iconKey: "evolutionMedium" },
  2: { label: "Hero", iconKey: "heroMedium" },
};

/**
 * Label of a card variant, empty for the regular card.
 *
 * @param evolutionLevel 1 for the evolution, 2 for the hero, 0 otherwise.
 * @returns The label, or "Variant <level>" for a level this app does not know.
 */
export function getCardVariantLabel(evolutionLevel: number): string {
  return CARD_VARIANTS[evolutionLevel]?.label ?? `Variant ${evolutionLevel}`;
}

export function getCardVariantName(
  name: string,
  evolutionLevel: number,
): string {
  const label = getCardVariantLabel(evolutionLevel);
  return label ? `${label} ${name}` : name;
}

export function getCardRarity(cardID: number, cards: CardMeta[]): string {
  return cards.find((c) => c.id === cardID)?.rarity ?? `#${cardID}`;
}

// Mirror costs the previously played card +1, so the card list has no
// elixirCost for it. Like the game, deck statistics count it as 1.5 elixir:
// since the May 2016 update the in-game Average Elixir Cost treats Mirror as a
// 1.5 elixir card (before that, it was left out as if the deck had 7 cards).
// The game shows no 4-card cycle. There Mirror costs the average of the three
// cheapest other cards +1, since in a cycle it copies one of those cheap cards
// (see calculateFourCardCycle in deck.tsx).
// Source: https://clashroyale.fandom.com/wiki/Mirror (Trivia, History)
export const MIRROR_ID = 28000006;
const MIRROR_DECK_ELIXIR_COST = 1.5;

/**
 * Elixir cost of a card for the deck's average elixir cost.
 *
 * @returns The cost, 1.5 for Mirror, or 0 if unknown. Check
 *   hasKnownElixirCost before showing a result based on it.
 */
export function getCardElixirCost(cardID: number, cards: CardMeta[]): number {
  if (cardID === MIRROR_ID) return MIRROR_DECK_ELIXIR_COST;
  return cards.find((c) => c.id === cardID)?.elixirCost ?? 0;
}

/**
 * Whether getCardElixirCost knows the card's cost: Mirror, or a card the card
 * list has an elixirCost for. Event cards outside the list have none.
 */
export function hasKnownElixirCost(cardID: number, cards: CardMeta[]): boolean {
  return (
    cardID === MIRROR_ID ||
    cards.find((c) => c.id === cardID)?.elixirCost != null
  );
}

// Tower troop id of the "None" category: a battle without tower data. Every
// battle should have a tower, so this is only a fallback. Clash Royale ids are
// never 0.
// TODO possibly fall back to TOWER_PRINCESS_ID
// that might be the only way support card is not there
// from a battle that was from before tower troops were added to the game.
// Tower troops came with the update of 13 December 2023: Princess Towers
// became Crown Towers, with Tower Princess as the first Tower Troop. Cannoneer
// followed in January 2024 (Season 55), Dagger Duchess in April 2024 (Season
// 58). Battles before 13 December 2023 had Tower Princess on every tower, but
// possibly not yet tracked in the battle data like it is today?
export const NO_SUPPORT_ID = 0;

// Tower troop the game falls back to, e.g. for copy links of None decks
export const TOWER_PRINCESS_ID = 159000000;

/**
 * Tower troop id of a deck, NO_SUPPORT_ID if the battles have no tower data.
 *
 * @param support The deck's support cards (at most one in practice).
 * @returns The id of the first support card, or NO_SUPPORT_ID.
 */
export function getSupportId(support: Card[] | undefined): number {
  return support?.[0]?.id ?? NO_SUPPORT_ID;
}

/**
 * Whether a card list entry is a tower troop. Decided by the list's category,
 * never by a missing elixir cost.
 */
export function isSupportCard(card: CardMeta): boolean {
  return card.category === "support";
}

/**
 * Lists the image URLs of a card variant, best first: the self-hosted WebP
 * copy, then the Clash Royale CDN original. The caller moves on to the next
 * URL when one fails to load.
 *
 * A missing variant does not fall back to the base art, which would pass a
 * new evolution or hero off as the regular card. The caller shows a labelled
 * placeholder instead.
 *
 * @param cardID Card id from the battle or deck.
 * @param evolutionLevel 1 for the evolution, 2 for the hero, 0 otherwise.
 * @param cards The card list.
 * @returns The URLs, empty if the card or its variant has no art (yet).
 */
export function getCardIconSources(
  cardID: number,
  evolutionLevel: number,
  cards: CardMeta[],
): string[] {
  const card = cards.find((c) => c.id === cardID);
  const key = CARD_VARIANTS[evolutionLevel]?.iconKey;
  if (!card || !key) return [];
  // iconUrls is guarded too: an entry without art falls back to the
  // placeholder instead of breaking every deck it appears in
  return [card.imageUrls?.[key], card.iconUrls?.[key]].filter(
    (url): url is string => Boolean(url),
  );
}
