import { whenApiIdle } from "../services/api/axios";

// Two parallel downloads keep the image connection busy without taking the
// bandwidth an API response or a visible image needs.
const CONCURRENCY = 2;
// Upper bound for waiting on an idle frame, in milliseconds. Without it a
// constantly busy main thread would starve the queue forever.
const IDLE_TIMEOUT_MS = 2000;

/**
 * Resolves once the window `load` event has fired, so preloading never
 * competes with the scripts, styles and fonts of the first paint.
 */
export function whenPageLoaded(): Promise<void> {
  if (document.readyState === "complete") return Promise.resolve();
  return new Promise((resolve) =>
    window.addEventListener("load", () => resolve(), { once: true }),
  );
}

function whenBrowserIdle(): Promise<void> {
  return new Promise((resolve) => {
    // Safari has no requestIdleCallback; a timeout still yields to rendering.
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(() => resolve(), { timeout: IDLE_TIMEOUT_MS });
    } else {
      window.setTimeout(resolve, 200);
    }
  });
}

function loadImage(url: string): Promise<void> {
  return new Promise((resolve) => {
    const img = new Image();
    img.fetchPriority = "low";
    img.decoding = "async";
    // A failed image is skipped; the real <img> retries when it renders.
    img.onload = img.onerror = () => resolve();
    // NOTE: No crossOrigin attribute, matching the <img> tags in card.tsx.
    // A CORS request would land under a different cache entry and be wasted.
    img.src = url;
  });
}

/**
 * Downloads images into the browser HTTP cache at the lowest priority the
 * page offers. Each download starts only on an idle frame and while no API
 * request is pending. Best effort: a download already running is not paused
 * when the page starts a request.
 *
 * @param urls Image URLs in the order they should be fetched.
 * @param signal Stops scheduling new downloads once aborted.
 * @returns A promise that resolves when the queue is drained or aborted.
 */
export async function preloadImagesInBackground(
  urls: string[],
  signal?: AbortSignal,
): Promise<void> {
  const queue = [...new Set(urls)];

  const worker = async () => {
    while (queue.length > 0 && !signal?.aborted) {
      await whenBrowserIdle();
      await whenApiIdle();
      // Another worker may have taken the last URL while this one waited.
      const url = queue.shift();
      if (!url || signal?.aborted) return;
      await loadImage(url);
    }
  };

  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
}
