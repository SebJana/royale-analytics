import { memo, useEffect, useState } from "react";
import Tooltip from "@mui/material/Tooltip";
import type { Card, CardMeta } from "../../types/cards";
import {
  getCardVariantName,
  getCardVariantLabel,
  getCardIconSources,
  isSupportCard,
} from "../../utils/getCardMetaFields";
import { getCardOutline, hasCardOutline } from "../../utils/color";
import { useRefreshCards } from "../../hooks/useCards";
import "./card.css";

export const CardComponent = memo(function CardComponent({
  card,
  cards,
  showTooltip = true,
  matched = false,
  showLevelLabel = true,
  isSupport = false,
}: Readonly<{
  card: Card;
  cards: CardMeta[];
  showTooltip?: boolean;
  matched?: boolean;
  // false moves the level into the tooltip, e.g. for the small tower slot
  showLevelLabel?: boolean;
  // A tower troop by the battle data, also while the card list lacks it
  isSupport?: boolean;
}>) {
  const evoLvl = card.evolutionLevel ?? 0; // If it's not an evolution, the evolutionLevel field is missing
  // Metadata from the official card list. Event cards (e.g. Super Lava Hound)
  // are never in it; they keep the name stored with the battle and get no
  // invented rarity, cost, or art.
  const meta = cards.find((c) => c.id === card.id);
  const baseName = meta?.name ?? (card.name || `#${card.id}`);
  const name = getCardVariantName(baseName, evoLvl);
  const rarity = meta?.rarity ?? "";
  // Uppercase the first letter of the rarity
  const rarityLabel = rarity
    ? rarity.charAt(0).toUpperCase() + rarity.slice(1)
    : "";
  const iconSources = getCardIconSources(card.id, evoLvl, cards);

  // An empty list is still loading, not missing this card. One refetch covers
  // a card released after the tab loaded its list. Event cards never join the
  // official list, so retrying for them would never end.
  const unknown = cards.length > 0 && !meta;
  const refreshCards = useRefreshCards();
  useEffect(() => {
    if (unknown) refreshCards();
  }, [unknown, refreshCards]);

  // URLs that failed to load here, so the next source takes over: the CDN
  // original after a self-hosted copy, then the placeholder. A refetched list
  // with a new URL tries that one.
  const [failedUrls, setFailedUrls] = useState<string[]>([]);
  const icon = iconSources.find((url) => !failedUrls.includes(url));
  const handleIconError = () => {
    if (!icon) return;
    setFailedUrls((urls) => [...urls, icon]);
    // A self-hosted URL fails when its set was removed, e.g. a list kept past
    // the server's retention. A newer list points to the current set. A
    // failing CDN URL means the art is not published yet, which a newer list
    // does not change.
    if (Object.values(meta?.imageUrls ?? {}).includes(icon)) refreshCards();
  };
  const variantLabel = getCardVariantLabel(evoLvl);

  const outlineImg = getCardOutline(rarity);

  const cardContent = (
    <div className="card-component-wrap">
      <img
        src={outlineImg}
        alt={`outline`}
        loading="lazy"
        width={285}
        height={420}
        className="card-component-outline"
      />
      {icon ? (
        <img
          src={icon}
          alt={name}
          loading="lazy"
          className="card-component-icon"
          onError={handleIconError}
        />
      ) : (
        // No source left: the card is unknown or loading, its art is not
        // published yet, or every URL failed. Names what it can, so a new card
        // stays recognizable without its art.
        <div
          className={`card-component-placeholder${hasCardOutline(rarity) ? " has-outline" : ""}`}
          role="img"
          aria-label={name}
        >
          <span className="card-component-placeholder-mark">?</span>
          {variantLabel && (
            <span className="card-component-placeholder-variant">
              {variantLabel}
            </span>
          )}
          {(meta || card.name) && (
            <span className="card-component-placeholder-name">{baseName}</span>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div className={`card-component-card${matched ? " is-matched" : ""}`}>
      {showTooltip ? (
        <Tooltip
          arrow
          // Let the tooltip close as soon as the pointer leaves the card.
          disableInteractive
          placement="auto"
          title={
            <div className="card-component-tooltip">
              <strong>{name}</strong>
              {isSupport || (meta && isSupportCard(meta)) ? (
                <span>Tower Troop</span>
              ) : (
                meta?.elixirCost != null && (
                  <span>{meta.elixirCost} Elixir</span>
                )
              )}
              {rarityLabel && <span>{rarityLabel}</span>}
              {!showLevelLabel && card.level != null && (
                <span>Level {card.level}</span>
              )}
              {!meta && cards.length > 0 && (
                <span>Not in the official card list</span>
              )}
              {matched && <span>Matches card filter</span>}
            </div>
          }
        >
          {cardContent}
        </Tooltip>
      ) : (
        cardContent
      )}

      {/* Only show level label if it exists (Battle page, not for Decks/Cards page) */}
      {showLevelLabel && card?.level != null && (
        <p className="card-component-level-label">Level {card.level}</p>
      )}
    </div>
  );
});
