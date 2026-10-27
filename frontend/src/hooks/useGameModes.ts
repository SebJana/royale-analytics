import { useQuery } from "@tanstack/react-query";
import { fetchGameModes } from "../services/api/gameModes";
import type { GameModes } from "../types/gameModes";
import { NECESSARY_QUERY_META } from "../utils/storage";

const min = 60_000;

export function useGameModes() {
  return useQuery<GameModes, Error>({
    queryKey: ["gameModes"],
    queryFn: fetchGameModes,
    staleTime: 5 * min, // Cache duration
    gcTime: 10 * min,
    refetchOnWindowFocus: false,
    meta: NECESSARY_QUERY_META,
  });
}
