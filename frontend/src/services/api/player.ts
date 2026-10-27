import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import type { Player } from "../../types/player";

export async function fetchPlayerProfile(playerTag: string): Promise<Player> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const response = await api.get<Player>(`/players/${tag}/profile`);
  return response.data;
}

/**
 * Fetch the ticket a page load needs to count a view of the player.
 *
 * @param playerTag - Tag of the viewed player, as the profile returns it
 * @param signal - Aborts the request when the page is left
 * @returns The signed ticket
 */
export async function fetchViewTicket(
  playerTag: string,
  signal?: AbortSignal,
): Promise<string> {
  const tag = encodeURIComponent(playerTag);
  const response = await api.get<{ ticket: string }>(
    `/players/${tag}/view-ticket`,
    { signal },
  );
  return response.data.ticket;
}

/**
 * Report a view of the player's profile. The backend answers the same
 * whether the view counted or not.
 *
 * @param playerTag - Tag of the viewed player, as the profile returns it
 * @param ticket - Ticket from fetchViewTicket
 * @param signal - Aborts the request when the page is left
 */
export async function sendProfileView(
  playerTag: string,
  ticket: string,
  signal?: AbortSignal,
): Promise<void> {
  const tag = encodeURIComponent(playerTag);
  await api.post(`/players/${tag}/view`, { ticket }, { signal });
}
