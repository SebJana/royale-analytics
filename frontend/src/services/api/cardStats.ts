import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import { setTimeRangeParams } from "./seasons";
import type { CardStats } from "../../types/cardStats";
import type { TimeRange } from "../../types/seasons";

export async function fetchCardStats(
  playerTag: string,
  range: TimeRange,
  gameModes?: string[],
): Promise<CardStats> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  setTimeRangeParams(params, range);

  // Append game modes if they exist as param
  if (gameModes?.length) {
    gameModes.forEach((mode) => params.append("game_modes", mode));
  }

  const url = `/players/${tag}/cards/stats?${params.toString()}`;

  const response = await api.get<CardStats>(url);
  return response.data;
}
