import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";

/**
 * Fetches the player's sync version, which rises with every sync that
 * stores battles of the player.
 *
 * @param playerTag - The player tag starting with '#'
 * @returns The current sync version
 */
export async function fetchSyncVersion(playerTag: string): Promise<number> {
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }
  const tag = encodeURIComponent(playerTag);
  const response = await api.get<{ syncVersion: number }>(
    `/players/${tag}/sync-version`,
  );
  return response.data.syncVersion;
}
