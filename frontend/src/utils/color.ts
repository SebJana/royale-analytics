import noOutlineImg from "../assets/cards/noOutline.png";
import rareOutlineImg from "../assets/cards/rareOutline.png";
import epicOutlineImg from "../assets/cards/epicOutline.png";
import legendaryOutlineImg from "../assets/cards/legendaryOutline.png";
import championOutlineImg from "../assets/cards/championOutline.png";

/**
 * Determines the outline color for a card based on its rarity.
 *
 * - Returns `null` if the card is an evolution (evolutions never have a colored outline).
 * - Returns `null` for "common" cards as well, since they already have a default gray outline.
 * - Returns a hex color string for rarities that use special outlines.
 *
 * @param rarity - The rarity of the card (e.g. "common", "rare", "epic", "legendary", "champion").
 * @param evolution - Whether the card is an evolution; if true, no outline color is returned.
 * @returns A hex color string for the outline, or `null` if no colored outline should be used.
 */
export function determineRarityColor(
  rarity: string,
  evolution: boolean,
): string | null {
  if (evolution) {
    return null;
  }

  const r = rarity.toLowerCase();
  switch (r) {
    case "common":
      return null;
    case "rare":
      return "#ff6c08";
    case "epic":
      return "#a149de";
    case "legendary":
      return "#cda2d2";
    case "champion":
      return "#fed328";
    default:
      return null;
  }
}

/**
 * Returns the outline image path for a given card rarity.
 *
 * @param rarity - The rarity of the card (e.g. "common", "rare", "epic", "legendary", "champion").
 * @returns The image path (string) of the corresponding outline PNG.
 *          Defaults to a blank image path if the rarity has no outline.
 */
export function getCardOutline(rarity: string): string {
  const r = rarity.toLowerCase();
  switch (r) {
    case "common":
      return noOutlineImg;
    case "rare":
      return rareOutlineImg;
    case "epic":
      return epicOutlineImg;
    case "legendary":
      return legendaryOutlineImg;
    case "champion":
      return championOutlineImg;
    default:
      return noOutlineImg;
  }
}

/**
 * Reports whether a rarity draws a visible outline around the card art.
 *
 * @param rarity - The rarity of the card, or anything else for an unknown card.
 * @returns False for common and unknown cards, whose outline image is blank.
 */
export function hasCardOutline(rarity: string): boolean {
  return getCardOutline(rarity) !== noOutlineImg;
}
