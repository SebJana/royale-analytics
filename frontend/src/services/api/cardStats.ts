import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import { setTimeRangeParams } from "./seasons";
import { setGameModeParams } from "./gameModes";
import type { CardStats } from "../../types/cardStats";
import type { TimeRange } from "../../types/seasons";
import type { GameModeQuery } from "../../types/gameModes";

export async function fetchCardStats(
  playerTag: string,
  range: TimeRange,
  gameModes?: GameModeQuery,
): Promise<CardStats> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  setTimeRangeParams(params, range);

  setGameModeParams(params, gameModes);

  const url = `/players/${tag}/cards/stats?${params.toString()}`;

  const response = await api.get<CardStats>(url);
  return response.data;
}
