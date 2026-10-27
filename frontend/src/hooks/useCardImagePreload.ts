import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cardsQueryOptions } from "./useCards";
import type { CardMeta } from "../types/cards";
import {
  preloadImagesInBackground,
  whenPageLoaded,
} from "../utils/preloadImages";

// Once per tab, also across StrictMode's double effect run.
let started = false;

/**
 * Warms the browser cache with every self-hosted card image so the player
 * pages render their cards without waiting on the network.
 *
 * Mounted once at the app root, so it also runs when a player page is the
 * first page opened (a bookmark, a reload, a shared link). The set is ~180
 * WebP images of 11-16 KB, ~2.2 MB in all, so it runs on every connection.
 *
 * It starts after the window load event, at low fetch priority, and starts
 * each download only while no API request is pending (see
 * preloadImagesInBackground). That keeps it out of the page's way in
 * practice, but is no strict ordering: a running download is not paused, and
 * the page's lazy images are not waited for.
 */
export function useCardImagePreload() {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (started) return;
    started = true;

    void (async () => {
      try {
        await whenPageLoaded();
        // fetchQuery honours the 15 minute staleTime, unlike ensureQueryData,
        // which would return a list restored from localStorage however old.
        // A stale list may point to an older image set and warm the wrong
        // files. Shares the cache entry useCards reads, so the player pages
        // skip their /cards request.
        const cards = await queryClient.fetchQuery(cardsQueryOptions);
        // Only self-hosted copies, the URLs getCardIconSources tries first.
        // Without a mirrored set (fresh install, failed first build) the CDN
        // originals would be ~27 MB; those load per card when shown instead.
        // Base art first. Evolution, hero, and any other variant shows up less
        // often, so it is the part worth losing if the user leaves early.
        const variants = (card: CardMeta) =>
          Object.entries(card.imageUrls ?? {}).filter(
            ([key]) => key !== "medium",
          );
        const urls = [
          ...cards.flatMap((card) => card.imageUrls?.medium ?? []),
          ...cards.flatMap((card) =>
            variants(card).flatMap(([, url]) => url ?? []),
          ),
        ];
        await preloadImagesInBackground(urls);
      } catch {
        // Purely an optimization; the pages load the images themselves.
        started = false;
      }
    })();
  }, [queryClient]);
}
