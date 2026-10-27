import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import { setTimeRangeParams } from "./seasons";
import { setGameModeParams } from "./gameModes";
import type { DailyStats } from "../../types/dailyStats";
import type { TimeRange } from "../../types/seasons";
import type { GameModeQuery } from "../../types/gameModes";

export async function fetchDailyStats(
  playerTag: string,
  range: TimeRange,
  gameModes?: GameModeQuery,
): Promise<DailyStats> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  setTimeRangeParams(params, range);

  setGameModeParams(params, gameModes);

  const url = `/players/${tag}/stats/daily?${params.toString()}`;

  const response = await api.get<DailyStats>(url);
  return response.data;
}
