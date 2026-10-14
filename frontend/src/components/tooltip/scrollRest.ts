// How long the page has to hold still before tooltips show again
const REST_MS = 200;

/**
 * Hides every tooltip while the page scrolls, until it has been at rest for
 * REST_MS. A tooltip explains what is under the pointer, which a moving page
 * keeps changing and the tooltip begins to jitter and flicker.
 * The pinned form strip stays under it while the battles pass,
 * and other tooltips ride along with their anchors until the mouse
 * moves again. A class on the root element (index.css) instead of state, so
 * the many tooltips of a battle list do not re-render on every scroll.
 */
export function hideTooltipsWhileScrolling() {
  const root = document.documentElement;
  let rest: number | undefined;
  window.addEventListener(
    "scroll",
    () => {
      root.classList.add("is-page-scrolling");
      window.clearTimeout(rest);
      rest = window.setTimeout(
        () => root.classList.remove("is-page-scrolling"),
        REST_MS,
      );
    },
    { passive: true },
  );
}
