import { memo } from "react";
import { CardComponent } from "../card/card";
import {
  getCardElixirCost,
  getCardRarity,
  getSupportId,
  hasKnownElixirCost,
  MIRROR_ID,
  NO_SUPPORT_ID,
  TOWER_PRINCESS_ID,
} from "../../utils/getCardMetaFields";
import type { Card, CardMeta } from "../../types/cards";
import { round } from "../../utils/number";
import { Copy } from "lucide-react";
import rareOutlineImg from "../../assets/cards/rareOutline.png";
import "./deck.css";

/**
 * Calculates the average elixir cost of a given deck.
 *
 * @param deck - The list of cards in the deck (each card has an `id`).
 * @param cards - Metadata containing card details including elixir costs.
 * @returns The average elixir cost of the deck, or 0 if the deck is empty.
 *
 */
function calcAverageElixirCost(deck: Card[], cards: CardMeta[]): number {
  let elixirSum = 0;
  const numberOfCards = deck.length;

  if (numberOfCards <= 0) {
    return 0;
  }

  for (const card of deck) {
    elixirSum += getCardElixirCost(card.id, cards);
  }
  const avgElixir = elixirSum / numberOfCards;
  return avgElixir;
}

/**
 * Calculates the 4 card cycle elixir cost of a given deck.
 * Effectively sums the elixir cost of the cheapest 4 cards in the deck, meaning
 * the fastest way to get a certain card back into rotation. Mirror costs the
 * average of the three cheapest other cards +1 here (see MIRROR_ID).
 *
 * @param deck - The list of cards in the deck (each card has an `id`).
 * @param cards - Metadata containing card details including elixir costs.
 * @returns The average elixir cost of the deck, or 0 if the deck is NOT 8 cards long.
 *
 */
function calculateFourCardCycle(deck: Card[], cards: CardMeta[]): number {
  const numberOfCards = deck.length;
  // Only calculate 4 Card Cycle for "regular" 8-card decks
  if (numberOfCards !== 8) {
    return 0;
  }
  const elixirAmount = deck
    .filter((card) => card.id !== MIRROR_ID)
    .map((card) => getCardElixirCost(card.id, cards))
    .sort((a, b) => a - b); // Sort elixir amounts ascending
  // Mirror replays the previous card for +1 elixir. In a cycle it copies one
  // of the cheap cards, so it costs the average of the three cheapest other
  // cards +1. It only counts if that is among the four cheapest.
  if (deck.some((card) => card.id === MIRROR_ID)) {
    const cheapestThree = elixirAmount.slice(0, 3);
    const mirrorCost =
      cheapestThree.reduce((sum, cost) => sum + cost, 0) /
        cheapestThree.length +
      1;
    elixirAmount.push(mirrorCost);
    elixirAmount.sort((a, b) => a - b);
  }

  let fourCardCycle = 0;
  for (let i = 0; i < 4; i++) {
    fourCardCycle += elixirAmount[i]; // Sum the elixir of the cheapest four cards
  }

  return fourCardCycle;
}

/**
 * Builds a Clash Royale deck share link.
 *
 * @param deck - The list of cards in the deck (each card has an `id`).
 * @param supportId - Tower troop id of the deck, NO_SUPPORT_ID if unknown.
 * @returns Deck copy link with card IDs and required query params.
 *
 */
function generateCopyLink(deck: Card[], supportId: number): string {
  const baseURL =
    "https://link.clashroyale.com/en/?clashroyale://copyDeck?deck=";
  let queryParam = baseURL;
  for (const card of deck) {
    queryParam += card.id; // Append the card id for every card in the deck
    queryParam += ";";
  }
  // Remove the trailing ";" for the last card id
  queryParam = queryParam.slice(0, -1);
  // Add necessary fields for the link
  const context = "&l=Royals";
  // Tower troop of the deck. The game needs one, so a deck without tower data
  // copies with Tower Princess, the default tower.
  const towerTroop = `&tt=${supportId === NO_SUPPORT_ID ? TOWER_PRINCESS_ID : supportId}`;
  const fullQueryParam = queryParam + context + towerTroop;

  return fullQueryParam;
}

/**
 * Small tower troop slot next to the card rows. Deliberately smaller than the
 * cards: the tower is an attribute of the deck, not a ninth card.
 */
function SupportSlot({
  support,
  cards,
  matchedSupportIds,
}: Readonly<{
  support: Card[];
  cards: CardMeta[];
  matchedSupportIds?: number[];
}>) {
  const supportId = getSupportId(support);
  const matched = matchedSupportIds?.includes(supportId) ?? false;

  // Clan War modes (river race, boat battles), friendlies and tournaments
  // report no tower troop: they use a special or fixed tower, not a missing
  // one. Nothing is shown. The slot only keeps its width as a blank where
  // decks sit side by side (battle.css); elsewhere the cards fill it.
  if (support.length === 0) {
    return (
      <div className="deck-component-support is-absent" aria-hidden="true" />
    );
  }

  return (
    <div className="deck-component-support">
      {support.map((s) => (
        <CardComponent
          key={s.id}
          card={s}
          cards={cards}
          matched={matched}
          isSupport
          // The full "Level 16" label is too wide for the slot. The short
          // caption below shows it; the tooltip names it in full.
          showLevelLabel={false}
        />
      ))}
      {support[0].level != null ? (
        // Battles carry the tower's level, deck statistics do not. Styled
        // like the cards' "Level 16" labels, shortened to fit the slot.
        <span className="deck-component-support-label is-level">
          Lvl {support[0].level}
        </span>
      ) : (
        <span className="deck-component-support-label">Tower</span>
      )}
    </div>
  );
}

export const DeckComponent = memo(function DeckComponent({
  deck,
  support,
  cards,
  elixirLeaked,
  matchedCards,
  matchedSupportIds,
  supportSide = "right",
}: Readonly<{
  deck: Card[];
  support: Card[]; // Tower troop, empty if the battle has no tower data
  cards: CardMeta[];
  elixirLeaked?: number; // Optional parameter, so that it can be used in battle display but also for deck statistics
  matchedCards?: Card[];
  matchedSupportIds?: number[]; // Tower troops selected in the card filter
  // Side of the tower troop slot. Battles put it on the outside of each deck.
  supportSide?: "left" | "right";
}>) {
  const cardsPerRow = 4;
  const rows: React.ReactElement[] = [];
  const matchedCardKeys = new Set(
    matchedCards?.map((card) => `${card.id}:${card.evolutionLevel ?? 0}`),
  );
  // Evolutions, then heroes and champions, then the rest. The sort is
  // stable, so cards of one group keep the order they came in.
  // NOTE: Heroes and champions share one deck slot in the game, so they sort
  // as one group, in their incoming order.
  const slotRank = (card: Card) => {
    if (card.evolutionLevel === 1) return 0;
    if (
      card.evolutionLevel === 2 ||
      getCardRarity(card.id, cards) === "champion"
    ) {
      return 1;
    }
    return 2;
  };
  const sortedDeck = [...deck].sort((a, b) => slotRank(a) - slotRank(b));
  for (let i = 0; i < sortedDeck.length; i += cardsPerRow) {
    const group = sortedDeck.slice(i, i + cardsPerRow); // put the cards into one row of display
    rows.push(
      <div key={`row-${i}`} className="deck-component-deck-row">
        {group.map((card) => (
          <CardComponent
            key={`${card.id}:${card.evolutionLevel ?? 0}`}
            card={card}
            cards={cards}
            matched={matchedCardKeys.has(
              `${card.id}:${card.evolutionLevel ?? 0}`,
            )}
          />
        ))}
      </div>,
    );
  }

  const averageElixir = calcAverageElixirCost(deck, cards);
  const roundedAvgElixir = round(averageElixir, 1);

  const fourCardCycle = calculateFourCardCycle(deck, cards);
  const roundedFourCardCycle = round(fourCardCycle, 2);

  // Event cards are not in the official card list, so their cost is unknown,
  // as is every cost while the list loads or when the list has no cost for a
  // card. Counting those as 0 elixir would show a wrong average as if it were
  // right. Mirror has no listed cost but counts as 1.5, as in the game (see
  // MIRROR_ID).
  const costsKnown = deck.every((card) => hasKnownElixirCost(card.id, cards));
  const unknownCostTitle =
    !costsKnown && cards.length > 0
      ? "Includes a card whose elixir cost is unknown, e.g. one outside the official card list"
      : undefined;

  // Works for both mobile and desktop because the Clash Royale Website handles
  // showing a qr code (desktop) and a copy link (mobile)
  const handleCopy = (event: React.MouseEvent) => {
    // A battle can wrap the deck in a link to the decks page, which must not
    // open as well
    event.preventDefault();
    event.stopPropagation();
    // Shown order, so the evolutions, heroes and champions come first, where
    // the game puts its evolution and hero/champion slots
    window.open(generateCopyLink(sortedDeck, getSupportId(support)), "_blank");
  };

  return (
    <>
      {/* TODO (potentially) add max deck row width/height*/}
      <div
        className={`deck-component-body${supportSide === "left" ? " support-left" : ""}`}
      >
        <div className="deck-component-cards">
          {deck.length === 0 ? (
            <div className="deck-component-empty-state">
              {/* Keep the usual two rows of four, even when a mode has no cards. */}
              {Array.from({ length: 2 }, (_, rowIndex) => (
                <div
                  key={rowIndex}
                  className="deck-component-deck-row deck-component-empty-row"
                  aria-hidden="true"
                >
                  {Array.from({ length: cardsPerRow }, (_, index) => (
                    <div key={index} className="deck-component-empty-card">
                      <img src={rareOutlineImg} alt="" />
                    </div>
                  ))}
                </div>
              ))}
              <span>No cards in this deck</span>
            </div>
          ) : (
            rows
          )}
        </div>
        <SupportSlot
          support={support}
          cards={cards}
          matchedSupportIds={matchedSupportIds}
        />
      </div>
      {/* TODO add elixir droplet icon to value*/}
      {/* Without a tower troop the bar ends where the cards end, also where
          a blank slot keeps the width (battle.css) */}
      <div
        className={`deck-component-footer${
          support.length === 0
            ? ` no-support${supportSide === "left" ? " support-left" : ""}`
            : ""
        }`}
      >
        <div className="deck-component-stats">
          <div className="deck-component-stat-item">
            <p className="deck-component-stat-value" title={unknownCostTitle}>
              {deck.length === 0 ? "—" : costsKnown ? roundedAvgElixir : "?"}
            </p>
            <p className="deck-component-stat-label">Avg Elixir</p>
          </div>
          <div className="deck-component-stat-item">
            <p className="deck-component-stat-value" title={unknownCostTitle}>
              {deck.length === 0
                ? "—"
                : costsKnown
                  ? roundedFourCardCycle
                  : "?"}
            </p>
            <p className="deck-component-stat-label">4-Card Cycle</p>
          </div>
          {/* Only display leaked elixir if it was passed into the component*/}
          {elixirLeaked != null && (
            <div className="deck-component-stat-item">
              <p className="deck-component-stat-value">{elixirLeaked}</p>
              <p className="deck-component-stat-label">Leaked Elixir</p>
            </div>
          )}
        </div>
        {deck.length > 0 && (
          <Copy className="deck-component-copy-button" onClick={handleCopy} />
        )}
      </div>
    </>
  );
});
