export type CardMeta = {
  name: string;
  id: number;
  maxLevel: number;
  maxEvolutionLevel?: number;
  elixirCost: number;
  // Clash Royale CDN originals (~150 KB PNGs). Further variants show up as
  // new keys.
  iconUrls: {
    medium: string;
    evolutionMedium?: string;
    heroMedium?: string;
    [key: string]: string | undefined;
  };
  // Self-hosted WebP copies from the data scraper, under the same keys as
  // iconUrls. A key is missing while the scraper could not mirror that image.
  imageUrls?: Record<string, string | undefined>;
  rarity: "common" | "rare" | "epic" | "legendary" | "champion";
};

export type CardsResponse = {
  items: CardMeta[];
};

export type Card = {
  name: string;
  id: number;
  level?: number;
  evolutionLevel?: number;
};
