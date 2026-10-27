import { useEffect } from "react";
import { fetchViewTicket, sendProfileView } from "../services/api/player";

// Time on the page before the view is reported.
// NOTE: Above VIEW_TICKET_MIN_AGE_S (3 s) in the backend settings, which
// rejects younger tickets. Update both together.
const VIEW_DWELL_MS = 3_500;

/**
 * Report one view of a player's page after it was visible for a moment.
 *
 * Runs once per player page, not per data request: polling, tab switches
 * and pagination never count. Only visible time counts, so a page opened in a
 * background tab is reported if it is looked at while its ticket is valid
 * (10 minutes), else it is lost. Nothing is
 * stored in the browser; the backend tells visitors apart by a daily changing
 * hash of the IP. Failures are ignored, a lost view
 * is harmless.
 *
 * @param playerTag - Tag of the viewed player, null until it is known
 */
export function useProfileView(playerTag: string | null) {
  useEffect(() => {
    if (!playerTag) return;
    const controller = new AbortController();
    let ticket: string | null = null;
    let remaining = VIEW_DWELL_MS;
    let visibleSince: number | null = null;
    let timer: number | undefined;
    // Terminal: one report per mount, however often the tab is hidden and
    // shown again afterwards.
    let reported = false;

    const report = () => {
      reported = true;
      if (ticket)
        sendProfileView(playerTag, ticket, controller.signal).catch(() => {});
    };
    // The timer only runs while the page is visible and the ticket is there,
    // and keeps the visible time already spent when the page is hidden.
    const update = () => {
      if (reported) return;
      const visible = document.visibilityState === "visible";
      if (visible && ticket && visibleSince === null) {
        visibleSince = Date.now();
        timer = window.setTimeout(report, remaining);
      } else if (!visible && visibleSince !== null) {
        remaining -= Date.now() - visibleSince;
        visibleSince = null;
        window.clearTimeout(timer);
      }
    };

    // The ticket is fetched right away even for a hidden page: the backend
    // only accepts it once it is a few seconds old anyway.
    fetchViewTicket(playerTag, controller.signal)
      .then((issued) => {
        if (controller.signal.aborted) return;
        ticket = issued;
        update();
      })
      .catch(() => {});
    document.addEventListener("visibilitychange", update);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, [playerTag]);
}
