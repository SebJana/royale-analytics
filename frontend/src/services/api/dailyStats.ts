import api from "./axios";
import { validatePlayerTagSyntax } from "../../utils/playerTag";
import type { DailyStats } from "../../types/dailyStats";

export async function fetchDailyStats(
  playerTag: string,
  startDate: string,
  endDate: string,
  gameModes?: string[],
): Promise<DailyStats> {
  // Throw error if an invalid player tag was passed
  if (!validatePlayerTagSyntax(playerTag)) {
    throw new Error("Invalid player tag");
  }

  const tag = encodeURIComponent(playerTag);
  const params = new URLSearchParams();
  params.set("start_date", startDate);
  params.set("end_date", endDate);

  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  params.set("timezone", timeZone);

  // Append game modes if they exist as param
  if (gameModes?.length) {
    gameModes.forEach((mode) => params.append("game_modes", mode));
  }

  const url = `/players/${tag}/stats/daily?${params.toString()}`;

  const response = await api.get<DailyStats>(url);
  return response.data;
}
