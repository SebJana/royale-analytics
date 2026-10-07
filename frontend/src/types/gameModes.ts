export type GameModes = Record<string, string>;
// e.g "#Ranked1v1_NewArena": "2025-09-19T18:32:12.746000"
// Game mode name: last seen timestamp

/**
 * Game mode filter of a statistics request: the modes to keep, or with
 * exclude the modes to leave out. No modes means all modes.
 */
export type GameModeQuery = {
  modes: string[];
  exclude: boolean;
};
