import type { CardMeta } from "../types/cards";

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

export function getCardElixirCost(cardID: number, cards: CardMeta[]): number {
  return cards.find((c) => c.id === cardID)?.elixirCost ?? 0;
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
  return [card.imageUrls?.[key], card.iconUrls[key]].filter(
    (url): url is string => Boolean(url),
  );
}
