import api from "./axios";
import type { GameModeQuery, GameModes } from "../../types/gameModes";

export async function fetchGameModes(): Promise<GameModes> {
  const response = await api.get<GameModes>("/game_modes");
  return response.data;
}

/**
 * Sets the game mode filter of a statistics request. Without modes nothing is
 * set, which requests all modes.
 * NOTE: The backend rejects game_modes and exclude_game_modes together and
 * caps each list at GAME_MODE_FILTER_MAX_MODES.
 *
 * @param params - Query parameters of the request, changed in place
 * @param query - The modes to keep, or to leave out
 */
export function setGameModeParams(
  params: URLSearchParams,
  query: GameModeQuery | undefined,
) {
  const name = query?.exclude ? "exclude_game_modes" : "game_modes";
  query?.modes.forEach((mode) => params.append(name, mode));
}
