import { useState, useEffect } from "react";
import type { Card, CardMeta } from "../../types/cards";
import { CardComponent } from "../card/card";
import { ChevronUp } from "lucide-react";
import { FilterSearch } from "../filterSearch/filterSearch";
import {
  getCardVariantName,
  isSupportCard,
  NO_SUPPORT_ID,
} from "../../utils/getCardMetaFields";
import rareOutlineImg from "../../assets/cards/rareOutline.png";
import "./cardFilter.css";

/**
 * Sorts cards by rarity (common, rare, epic, legendary, champion) and then by elixir cost within the same rarity.
 * @param cards - Array of card metadata to sort
 * @returns Sorted array of card metadata
 */
function sortCards(cards: CardMeta[]): CardMeta[] {
  // Ascending order of rarities
  const rarityOrder = ["common", "rare", "epic", "legendary", "champion"];
  const rarityMap = new Map(rarityOrder.map((r, i) => [r, i]));

  return [...cards].sort((a, b) => {
    // 1. Compare (sort by) rarity
    const rarityDiff =
      (rarityMap.get(a.rarity) ?? Infinity) -
      (rarityMap.get(b.rarity) ?? Infinity);

    if (rarityDiff !== 0) return rarityDiff;

    // 2. Within same rarity, compare (sort by) elixirCost
    return (a.elixirCost ?? 0) - (b.elixirCost ?? 0); // ascending
  });
}

/**
 * Creates a Card object with the specified ID and evolution level.
 * @param cardId - The unique identifier for the card
 * @param cardName - The name of the specified card
 * @param cardEvolutionLevel - The evolution level of the card (0 for regular cards)
 * @returns A Card object with the specified properties
 */
function createCard(
  cardId: number,
  cardName: string,
  cardEvolutionLevel: number,
): Card {
  const card: Card = {
    name: getCardVariantName(cardName, cardEvolutionLevel),
    id: cardId,
  };

  if (cardEvolutionLevel > 0) {
    card.evolutionLevel = cardEvolutionLevel;
  }

  return card;
}

// TODO create lookup interface/type for evolution types and their corresponding maxEvolutionLevel
/**
 * Creates a list of Card objects from card metadata, including both evolution and regular versions.
 * Evolution cards are added first, followed by regular versions of all cards.
 * @param cards - Array of card metadata to process
 * @returns Array of Card objects including both evolved and regular versions
 */
function createCardList(cards: CardMeta[]): Card[] {
  const cardList: Card[] = [];

  // NOTE: maxEvolutionLevel 3 creates both Evolution (level 1) and Hero (level 2).
  // If more variant levels are added, update these loops and CARD_VARIANTS
  // (getCardMetaFields.ts).

  // Add in multiple loops, so that order of cards stays how it was previously sorted

  // All cards that have a regular evolution
  for (const card of cards) {
    const maxEvoLvl = card.maxEvolutionLevel ?? 0;
    if (maxEvoLvl === 1 || maxEvoLvl === 3) {
      // Regular Evolution (Level 1)
      const c = createCard(card.id, card.name, 1);
      cardList.push(c);
    }
  }

  // All cards that have a hero evolution
  for (const card of cards) {
    const maxEvoLvl = card.maxEvolutionLevel ?? 0;
    if (maxEvoLvl === 2 || maxEvoLvl === 3) {
      // Hero Evolution (Level 2)
      const c = createCard(card.id, card.name, 2);
      cardList.push(c);
    }
  }

  // All regular cards
  for (const card of cards) {
    const c = createCard(card.id, card.name, 0);
    cardList.push(c);
  }

  return cardList;
}

/**
 * Sorts tower troops by rarity, then name. They cost no elixir, so the
 * regular card sort does not apply.
 * @param towers - Tower troop metadata to sort
 * @returns Sorted array of tower troop metadata
 */
function sortTowers(towers: CardMeta[]): CardMeta[] {
  const rarityOrder = ["common", "rare", "epic", "legendary", "champion"];
  const rarityMap = new Map(rarityOrder.map((r, i) => [r, i]));
  return [...towers].sort(
    (a, b) =>
      (rarityMap.get(a.rarity) ?? Infinity) -
        (rarityMap.get(b.rarity) ?? Infinity) || a.name.localeCompare(b.name),
  );
}

export function CardFilter({
  cards,
  selected,
  onCardsChange,
  selectedSupportIds,
  onSupportIdsChange,
  showNoSupportOption = false,
  includeCardFilterMode,
  onCardFilterModeChange,
}: Readonly<{
  cards: CardMeta[];
  selected: Card[];
  onCardsChange: (next: Card[]) => void; // emit cards
  selectedSupportIds: number[]; // Tower troops, NO_SUPPORT_ID for None
  onSupportIdsChange: (next: number[]) => void;
  // Offer None, for decks of battles without tower data. Every battle should
  // have a tower, so the page only sets this when such decks exist.
  showNoSupportOption?: boolean;
  includeCardFilterMode?: boolean; // Optional prop to control filter mode
  onCardFilterModeChange?: (next: boolean) => void;
}>) {
  const sortedCards = sortCards(cards.filter((c) => !isSupportCard(c)));
  const cardOptions = createCardList(sortedCards);
  const towerOptions = sortTowers(cards.filter(isSupportCard));

  const [isExpanded, setIsExpanded] = useState(false); // init with hidden option
  const [searchTerm, setSearchTerm] = useState("");
  const normalizedSearch = searchTerm.trim().toLocaleLowerCase();
  const filteredCardOptions = normalizedSearch
    ? cardOptions.filter((card) =>
        card.name.toLocaleLowerCase().includes(normalizedSearch),
      )
    : cardOptions;
  const filteredTowerOptions = normalizedSearch
    ? towerOptions.filter((tower) =>
        tower.name.toLocaleLowerCase().includes(normalizedSearch),
      )
    : towerOptions;
  // Also offered while selected, so a restored selection can be undone
  const showNoSupport =
    (showNoSupportOption || selectedSupportIds.includes(NO_SUPPORT_ID)) &&
    (!normalizedSearch || "none".includes(normalizedSearch));

  // Keep track of the selected matching mode
  const [localFilterMode, setLocalFilterMode] = useState(
    includeCardFilterMode ?? true,
  );

  // Sync local state with prop changes, so that the parent component knows the selected option
  useEffect(() => {
    setLocalFilterMode(includeCardFilterMode ?? true);
  }, [includeCardFilterMode]);

  // Handle toggle of filter mode
  const toggleFilterMode = () => {
    const newMode = !localFilterMode;
    setLocalFilterMode(newMode);
    if (onCardFilterModeChange) {
      onCardFilterModeChange(newMode);
    }
    // A deck has one tower, so Include mode allows one. Keep the most
    // recently selected.
    if (newMode && selectedSupportIds.length > 1) {
      onSupportIdsChange(selectedSupportIds.slice(-1));
    }
  };

  const toggleSupport = (id: number) => {
    if (selectedSupportIds.includes(id)) {
      onSupportIdsChange(selectedSupportIds.filter((s) => s !== id));
    } else if (localFilterMode) {
      // Include mode: picking a tower replaces the previous one
      onSupportIdsChange([id]);
    } else {
      onSupportIdsChange([...selectedSupportIds, id]);
    }
  };

  // Check if the given card is in the selection pool, with same id and evolution level
  const isSelected = (card: Card) =>
    selected.some(
      (s) =>
        s.id === card.id &&
        (s.evolutionLevel ?? 0) === (card.evolutionLevel ?? 0),
    );

  const toggle = (card: Card) => {
    const isCurrentlySelected = isSelected(card);
    // If card is already selected, remove it
    if (isCurrentlySelected) {
      // Remove this card
      onCardsChange(
        selected.filter(
          (s) =>
            !(
              // CardId and Evo Level have to match, then remove that card
              s.id === card.id &&
              (s.evolutionLevel ?? 0) === (card.evolutionLevel ?? 0)
            ),
        ),
      );
    } else {
      // Append this card to the selection
      onCardsChange([...selected, card]);
    }
  };

  const clearAll = () => {
    onCardsChange([]);
    onSupportIdsChange([]);
  };

  return (
    <div className="card-filter-container">
      <button
        type="button"
        className="card-filter-component-header"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <span className="card-filter-component-title">Cards</span>
        <ChevronUp
          className={`card-filter-component-toggle ${
            !isExpanded ? "collapsed" : ""
          }`}
        />
      </button>
      {isExpanded && onCardFilterModeChange && (
        <div className="card-filter-component-mode-toggle">
          <div className="card-filter-component-mode-label">
            <span>
              {localFilterMode
                ? "Only show decks that include all selected cards and the selected tower troop"
                : "Show decks that share the most cards and tower troops with your selection"}
            </span>
            <div className="card-filter-component-toggle-container">
              <span className="card-filter-component-toggle-label">Match</span>
              <label
                className="card-filter-component-slide-toggle"
                aria-label="Toggle card filter mode"
              >
                <input
                  type="checkbox"
                  checked={localFilterMode}
                  onChange={toggleFilterMode}
                  className="card-filter-component-toggle-input"
                />
                <span className="card-filter-component-toggle-slider"></span>
              </label>
              <span className="card-filter-component-toggle-label">
                Include
              </span>
            </div>
          </div>
        </div>
      )}
      {isExpanded && (
        <FilterSearch
          value={searchTerm}
          onChange={setSearchTerm}
          placeholder="Search cards"
        />
      )}
      <div
        id="card-filter-grid"
        className={`card-filter-component-grid ${!isExpanded ? "hidden" : ""}`}
      >
        {filteredCardOptions.map((c, i) => (
          <button
            key={`${c.id}-${i}`}
            type="button"
            className={`card-filter-item ${isSelected(c) ? "is-selected" : ""}`}
            onClick={() => toggle(c)}
            aria-label={c.name}
          >
            <CardComponent card={c} cards={cards ?? []} />
          </button>
        ))}
        {filteredCardOptions.length === 0 &&
          filteredTowerOptions.length === 0 &&
          !showNoSupport && (
            <p className="card-filter-empty">No cards found.</p>
          )}
        {(filteredTowerOptions.length > 0 || showNoSupport) && (
          <div className="card-filter-tower-row">
            <span className="card-filter-tower-title">Tower Troops</span>
            <div className="card-filter-tower-grid">
              {filteredTowerOptions.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`card-filter-item ${
                    selectedSupportIds.includes(t.id) ? "is-selected" : ""
                  }`}
                  onClick={() => toggleSupport(t.id)}
                  aria-label={t.name}
                  aria-pressed={selectedSupportIds.includes(t.id)}
                >
                  <CardComponent
                    card={{ id: t.id, name: t.name }}
                    cards={cards}
                  />
                </button>
              ))}
              {showNoSupport && (
                <button
                  type="button"
                  className={`card-filter-item card-filter-none-item ${
                    selectedSupportIds.includes(NO_SUPPORT_ID)
                      ? "is-selected"
                      : ""
                  }`}
                  onClick={() => toggleSupport(NO_SUPPORT_ID)}
                  aria-label="No tower troop recorded"
                  aria-pressed={selectedSupportIds.includes(NO_SUPPORT_ID)}
                  title="Decks of battles without tower troop data"
                >
                  <img src={rareOutlineImg} alt="" />
                  <span>None</span>
                </button>
              )}
            </div>
          </div>
        )}
        <div className="card-filter-component-actions">
          <button
            type="button"
            className="card-filter-component-action-button card-filter-component-clear"
            onClick={clearAll}
          >
            Clear
          </button>
        </div>
      </div>
    </div>
  );
}
