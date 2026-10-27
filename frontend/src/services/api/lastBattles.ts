import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import type { LastBattles } from "../../types/lastBattles";

const DEFAULT_LIMIT = 10;

export async function fetchLastBattles(
  playerTag: string,
  before?: string,
  limit: number = DEFAULT_LIMIT,
): Promise<LastBattles> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (before) params.set("before", before);

  const url = `/players/${tag}/battles?${params.toString()}`;

  const response = await api.get<LastBattles>(url);
  return response.data;
}
