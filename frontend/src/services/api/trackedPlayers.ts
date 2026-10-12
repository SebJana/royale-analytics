import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import type {
  ExplorePage,
  PlayerCount,
  PlayerSearchResponse,
} from "../../types/players";

/**
 * Search tracked players by name or tag, best match first.
 *
 * @param query - Text as typed. A leading "#" ranks tag matches first.
 * @param signal - Aborts the request once a newer query replaces it
 * @returns Up to SEARCH_RESULT_LIMIT ranked players (an API setting), and
 *   whether more players match
 */
export async function searchTrackedPlayers(
  query: string,
  signal?: AbortSignal,
): Promise<PlayerSearchResponse> {
  const response = await api.get<PlayerSearchResponse>("/players/search", {
    params: { q: query },
    signal,
  });
  return response.data;
}

/**
 * Fetch one page of the most viewed tracked players.
 *
 * @param cursor - Last rank of the previous page, 0 for the first
 * @param limit - Players in the page (the API allows up to 50)
 * @param signal - Aborts the request when the component unmounts
 * @returns The players, most viewed first, and the cursor of the next page
 */
export async function fetchExplorePlayers(
  cursor: number,
  limit: number,
  signal?: AbortSignal,
): Promise<ExplorePage> {
  const response = await api.get<ExplorePage>("/players/explore", {
    params: { cursor, limit },
    signal,
  });
  return response.data;
}

export async function fetchAllTrackedPlayersCount(): Promise<PlayerCount> {
  const response = await api.get<PlayerCount>("/players/count");
  return response.data;
}

export async function trackPlayer(
  playerTag: string,
): Promise<{ status: string; tag: string }> {
  const tag = playerTag.trim();
  if (!validatePlayerTagSyntax(tag)) {
    throw new Error("Invalid player tag. Enter a tag like #YYRJQY28.");
  }

  const response = await api.post(`/players/${encodeURIComponent(tag)}`);
  return response.data;
}

export async function untrackPlayer(
  playerTag: string,
): Promise<{ status: string; tag: string }> {
  const tag = playerTag.trim();
  if (!validatePlayerTagSyntax(tag)) {
    throw new Error("Invalid player tag. Enter a tag like #YYRJQY28.");
  }

  const response = await api.delete(`/players/${encodeURIComponent(tag)}`);
  return response.data;
}
