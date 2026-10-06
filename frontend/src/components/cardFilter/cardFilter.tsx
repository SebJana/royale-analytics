import { useState, useEffect } from "react";
import type { Card, CardMeta } from "../../types/cards";
import { CardComponent } from "../card/card";
import { ChevronUp } from "lucide-react";
import { FilterSearch } from "../filterSearch/filterSearch";
import { SegmentedControl } from "../segmentedControl/segmentedControl";
import {
  getCardVariantName,
  isSupportCard,
} from "../../utils/getCardMetaFields";
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

// NOTE: Match the DECK_FILTER_* limits in the backend's settings.py. Include
// mode keeps decks with every selected card, so a deck's 8 cards bound it.
// The 12-card ClanWar_BoatBattle defenses are deliberately not covered:
// nobody filters for a full defense. Match mode and exclusion may name more.
const MAX_INCLUDE_CARDS = 8;
const MAX_CARDS = 32;
const MAX_SUPPORT = 8;

/**
 * Whether two cards are the same variant: same id and evolution level.
 */
function isSameCard(a: Card, b: Card): boolean {
  return a.id === b.id && (a.evolutionLevel ?? 0) === (b.evolutionLevel ?? 0);
}

export function CardFilter({
  cards,
  selected,
  onCardsChange,
  selectedSupportIds,
  onSupportIdsChange,
  excluded,
  onExcludedChange,
  excludedSupportIds,
  onExcludedSupportIdsChange,
  includeCardFilterMode,
  onCardFilterModeChange,
}: Readonly<{
  cards: CardMeta[];
  selected: Card[];
  onCardsChange: (next: Card[]) => void; // emit cards
  selectedSupportIds: number[]; // Tower troop ids
  onSupportIdsChange: (next: number[]) => void;
  excluded: Card[]; // Cards no shown deck may contain
  onExcludedChange: (next: Card[]) => void;
  excludedSupportIds: number[];
  onExcludedSupportIdsChange: (next: number[]) => void;
  includeCardFilterMode?: boolean; // Optional prop to control filter mode
  onCardFilterModeChange?: (next: boolean) => void;
}>) {
  const sortedCards = sortCards(cards.filter((c) => !isSupportCard(c)));
  const cardOptions = createCardList(sortedCards);
  // NOTE: Decks without a tower troop (NO_SUPPORT_ID: Clan War, friendlies,
  // tournaments) are not selectable here, only the card list's tower troops
  // are. Game modes narrow those decks down instead. The backend still
  // accepts NO_SUPPORT_ID in support_ids and exclude_support_ids.
  const towerOptions = sortTowers(cards.filter(isSupportCard));

  const [isExpanded, setIsExpanded] = useState(false); // init with hidden option
  const [searchTerm, setSearchTerm] = useState("");
  // What clicking an unmarked card does. Only a way of picking, so it is not
  // part of the filter state.
  const [pickExcludes, setPickExcludes] = useState(false);
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

  // Keep track of the selected matching mode
  const [localFilterMode, setLocalFilterMode] = useState(
    includeCardFilterMode ?? true,
  );

  // Sync local state with prop changes, so that the parent component knows the selected option
  useEffect(() => {
    setLocalFilterMode(includeCardFilterMode ?? true);
  }, [includeCardFilterMode]);

  const maxSelectedCards = localFilterMode ? MAX_INCLUDE_CARDS : MAX_CARDS;

  // true for Include, false for Match
  const changeFilterMode = (newMode: boolean) => {
    setLocalFilterMode(newMode);
    if (onCardFilterModeChange) {
      onCardFilterModeChange(newMode);
    }
    // Include mode has lower limits, a deck has one tower. Keep the most
    // recently selected, so the backend never rejects the request.
    if (newMode && selectedSupportIds.length > 1) {
      onSupportIdsChange(selectedSupportIds.slice(-1));
    }
    if (newMode && selected.length > MAX_INCLUDE_CARDS) {
      onCardsChange(selected.slice(-MAX_INCLUDE_CARDS));
    }
  };

  // A marked tower troop gets unmarked, an unmarked one marked as the picker says
  const toggleSupport = (id: number) => {
    if (selectedSupportIds.includes(id)) {
      onSupportIdsChange(selectedSupportIds.filter((s) => s !== id));
    } else if (excludedSupportIds.includes(id)) {
      onExcludedSupportIdsChange(excludedSupportIds.filter((s) => s !== id));
    } else if (pickExcludes) {
      if (excludedSupportIds.length < MAX_SUPPORT) {
        onExcludedSupportIdsChange([...excludedSupportIds, id]);
      }
    } else if (localFilterMode) {
      // Include mode: picking a tower replaces the previous one
      onSupportIdsChange([id]);
    } else if (selectedSupportIds.length < MAX_SUPPORT) {
      onSupportIdsChange([...selectedSupportIds, id]);
    }
  };

  // Check if the given card is in the selection pool, with same id and evolution level
  const isSelected = (card: Card) => selected.some((s) => isSameCard(s, card));
  const isExcluded = (card: Card) => excluded.some((s) => isSameCard(s, card));

  // A marked card gets unmarked, an unmarked one marked as the picker says.
  // At the limit a click does nothing; the Selected bar shows the count.
  const toggle = (card: Card) => {
    if (isSelected(card)) {
      onCardsChange(selected.filter((s) => !isSameCard(s, card)));
    } else if (isExcluded(card)) {
      onExcludedChange(excluded.filter((s) => !isSameCard(s, card)));
    } else if (pickExcludes) {
      if (excluded.length < MAX_CARDS) onExcludedChange([...excluded, card]);
    } else if (selected.length < maxSelectedCards) {
      onCardsChange([...selected, card]);
    }
  };

  const clearAll = () => {
    onCardsChange([]);
    onSupportIdsChange([]);
    onExcludedChange([]);
    onExcludedSupportIdsChange([]);
  };

  const markClass = (isMarkedSelected: boolean, isMarkedExcluded: boolean) => {
    if (isMarkedSelected) return "is-selected";
    if (isMarkedExcluded) return "is-excluded";
    return "";
  };

  // The state in words for screen readers, since only color shows it.
  // aria-pressed cannot, as it has no second "on" state for excluded.
  const markLabel = (
    name: string,
    isMarkedSelected: boolean,
    isMarkedExcluded: boolean,
  ) => {
    if (isMarkedSelected) return `${name}, selected`;
    if (isMarkedExcluded) return `${name}, excluded`;
    return `${name}, not selected`;
  };

  const towerName = (id: number) =>
    towerOptions.find((t) => t.id === id)?.name ?? `#${id}`;

  // Renders a tower troop the same way in the grid and the Selected bar,
  // where a click removes it. startsTowers draws the divider
  // to the cards on this tower, so it wraps to a new row together with it.
  const renderTower = (
    id: number,
    inSelectedBar = false,
    startsTowers = false,
  ) => {
    const isMarkedSelected = selectedSupportIds.includes(id);
    const isMarkedExcluded = excludedSupportIds.includes(id);
    const extraClass = [
      inSelectedBar ? "card-filter-selected-item" : "",
      startsTowers ? "card-filter-selected-first-tower" : "",
    ].join(" ");
    const label = markLabel(towerName(id), isMarkedSelected, isMarkedExcluded);
    const ariaLabel = inSelectedBar ? `Remove ${label}` : label;
    return (
      <button
        key={id}
        type="button"
        className={`card-filter-item ${extraClass} ${markClass(
          isMarkedSelected,
          isMarkedExcluded,
        )}`}
        onClick={() => toggleSupport(id)}
        aria-label={ariaLabel}
      >
        <CardComponent card={{ id, name: towerName(id) }} cards={cards} />
      </button>
    );
  };

  const hasMarks =
    selected.length > 0 ||
    selectedSupportIds.length > 0 ||
    excluded.length > 0 ||
    excludedSupportIds.length > 0;

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
      {/* Shown while collapsed too, so the active card filter stays visible */}
      {hasMarks && (
        <div className="card-filter-selected">
          <span className="card-filter-selected-title">
            Selected ({selected.length}/{maxSelectedCards})
            {excluded.length > 0 &&
              ` · Excluded (${excluded.length}/${MAX_CARDS})`}
          </span>
          <div className="card-filter-selected-items">
            {selected.map((c) => (
              <button
                key={`selected-${c.id}-${c.evolutionLevel ?? 0}`}
                type="button"
                className="card-filter-item card-filter-selected-item is-selected"
                onClick={() => toggle(c)}
                aria-label={`Remove ${c.name}, selected`}
              >
                <CardComponent card={c} cards={cards} />
              </button>
            ))}
            {excluded.map((c) => (
              <button
                key={`excluded-${c.id}-${c.evolutionLevel ?? 0}`}
                type="button"
                className="card-filter-item card-filter-selected-item is-excluded"
                onClick={() => toggle(c)}
                aria-label={`Remove ${c.name}, excluded`}
              >
                <CardComponent card={c} cards={cards} />
              </button>
            ))}
            {/* Tower troops follow in the same flow, so they never take a
                row of their own; the first one carries the divider */}
            {[...selectedSupportIds, ...excludedSupportIds].map((id, i) =>
              renderTower(
                id,
                true,
                i === 0 && (selected.length > 0 || excluded.length > 0),
              ),
            )}
          </div>
        </div>
      )}
      {isExpanded && (
        <div className="card-filter-controls">
          {onCardFilterModeChange && (
            <SegmentedControl
              title="Filter Mode"
              ariaLabel="Card filter mode"
              options={[
                { value: "include", label: "Include" },
                { value: "match", label: "Match" },
              ]}
              value={localFilterMode ? "include" : "match"}
              onChange={(mode) => changeFilterMode(mode === "include")}
              hint={{
                match: "Decks ranked by shared cards",
                include: "Decks need every selected card",
              }}
            />
          )}
          <SegmentedControl
            title="Click"
            ariaLabel="What clicking a card does"
            options={[
              { value: "select", label: "Select" },
              { value: "exclude", label: "Exclude", danger: true },
            ]}
            value={pickExcludes ? "exclude" : "select"}
            onChange={(pick) => setPickExcludes(pick === "exclude")}
            hint={{
              select: "Clicked cards are wanted",
              exclude: "Clicked cards rule a deck out",
            }}
          />
          <button
            type="button"
            className="card-filter-component-action-button card-filter-component-clear card-filter-controls-clear"
            onClick={clearAll}
            disabled={!hasMarks}
          >
            Clear
          </button>
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
            className={`card-filter-item ${markClass(
              isSelected(c),
              isExcluded(c),
            )}`}
            onClick={() => toggle(c)}
            aria-label={markLabel(c.name, isSelected(c), isExcluded(c))}
          >
            <CardComponent card={c} cards={cards ?? []} />
          </button>
        ))}
        {filteredCardOptions.length === 0 &&
          filteredTowerOptions.length === 0 && (
            <p className="card-filter-empty">No cards found.</p>
          )}
        {filteredTowerOptions.length > 0 && (
          <div className="card-filter-tower-row">
            <span className="card-filter-tower-title">Tower Troops</span>
            <div className="card-filter-tower-grid">
              {filteredTowerOptions.map((t) => renderTower(t.id))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
