import axios from "axios";
import type { Status } from "../types/status";

/**
 * Turns a failed add or remove of a tracked player into a message for the
 * user. Shared by the home page forms and the player page's "Track again",
 * so the wording has to fit both: there is no tag field on the player page.
 *
 * NOTE: Match the error codes of the add and remove routes in the backend's
 * routers/players_tracked.py.
 *
 * @param error - Error of the trackPlayer or untrackPlayer request
 * @returns A message that says what failed and what to do about it
 */
export function getTrackingErrorMessage(error: unknown): string {
  if (
    !axios.isAxiosError<{
      detail?: string | { code?: string; message?: string };
    }>(error)
  ) {
    return error instanceof Error
      ? error.message
      : "Something went wrong. Please try again.";
  }

  if (!error.response) return "Could not reach the server. Please try again.";

  const { status, data } = error.response;
  const detail = data?.detail;
  const code = typeof detail === "object" ? detail?.code : undefined;

  // Check the backend code first; a 502 can also come from Clash Royale.
  if (code === "INVALID_PLAYER_TAG")
    return "Invalid player tag. Enter a tag like #YYRJQY28.";
  if (code === "PLAYER_NOT_FOUND")
    return "No Clash Royale player has this tag.";
  if (code === "PLAYER_NOT_TRACKED") return "That player isn't being tracked.";
  if (code === "PLAYER_PROTECTED")
    return "This is one of last season's top players. It can be removed once the next season's top list replaces it.";
  if (code === "REMOVE_PLAYER_TOKEN_USED_UP")
    return "This verification removed the most players it can. Verify again to remove more.";
  if (code === "REMOVE_PLAYER_TOKEN_EXPIRED")
    return "Your verification expired. Verify again.";
  if (code === "TRACKING_BUSY")
    return "Many players are being added right now. Please try again in a few seconds.";
  if (code === "TRACKING_CAPACITY_REACHED")
    return "The maximum number of tracked players is reached. No new players can be added right now.";
  if (code === "CR_API_AUTH_FAILED")
    return (
      "Clash Royale rejected the API connection, so this player can't be tracked right now. " +
      "Please try again later."
    );
  if (code === "CR_API_MAINTENANCE")
    return (
      "Clash Royale is currently undergoing maintenance, so this player can't be tracked right now. " +
      "Please try again later."
    );
  if (code === "CR_API_UNAVAILABLE" || code === "CR_API_INVALID_RESPONSE")
    return "Could not check the player with Clash Royale. Please try again later.";
  if (status === 401 || status === 403)
    return "Authorization failed. Please verify again.";
  if (status === 429) return "Too many requests. Please try again shortly.";
  if (status >= 500)
    return "The server could not complete the request. Please try again later.";

  return (
    (typeof detail === "string" ? detail : detail?.message) ??
    "Something went wrong. Please try again."
  );
}

/**
 * Turns a failed add, remove or "Track again" into a status. Rate limits and
 * outages pass on their own, so they read as "wait" rather than as a mistake
 * in the tag.
 *
 * @param error - Error of the trackPlayer or untrackPlayer request
 * @param title - What failed, e.g. "Couldn't add player"
 * @returns The status for StatusMessage
 */
export function getTrackingErrorStatus(error: unknown, title: string): Status {
  const response = axios.isAxiosError<{ detail?: { code?: string } | string }>(
    error,
  )
    ? error.response
    : undefined;
  const detail = response?.data?.detail;
  const code = typeof detail === "object" ? detail?.code : undefined;
  const isTemporary =
    response?.status === 429 ||
    code === "CR_API_MAINTENANCE" ||
    code === "CR_API_UNAVAILABLE" ||
    code === "TRACKING_BUSY";
  return {
    tone: isTemporary ? "wait" : "error",
    title,
    message: getTrackingErrorMessage(error),
  };
}
