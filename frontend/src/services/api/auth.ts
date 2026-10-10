import api from "./axios";
import type {
  CaptchaResponse,
  CaptchaVerifyRequest,
  CaptchaTokenResponse,
  WordleResponse,
  HalliGalliGameResponse,
  HalliGalliStatusResponse,
  HalliGalliRevealResponse,
  HalliGalliRoundResponse,
  WordleVerifyRequest,
  SecurityQuestionsRequest,
  SecurityTokenResponse,
  RemovePlayerTokenResponse,
} from "../../types/auth";

// Capability tokens authorize the next authentication step. Every HTTP call
// that consumes one sends it as Authorization: Bearer <token>, never in JSON.
// NOTE: Each token only opens a few next steps (budgets in the backend's
// settings.py); a used-up one answers with a *_TOKEN_USED_UP code.

/**
 * Step 1.1: claims a CAPTCHA challenge.
 *
 * @returns The challenge ID, which also names its image
 */
export async function getCaptchaId(): Promise<CaptchaResponse> {
  const response = await api.post<CaptchaResponse>("/auth/captcha_id");
  return response.data;
}

/**
 * Step 1.2: loads a claimed CAPTCHA's image, available for a minute.
 *
 * @param captchaId - ID from getCaptchaId
 * @returns The PNG
 */
export async function getCaptchaImage(captchaId: string): Promise<Blob> {
  const response = await api.get(`/auth/captcha_image/${captchaId}`, {
    responseType: "blob",
  });
  return response.data;
}

/**
 * Step 1.3: checks the CAPTCHA answer.
 *
 * @param request - Challenge ID and the typed text
 * @returns The CAPTCHA token for the Wordle step
 */
export async function verifyCaptcha(
  request: CaptchaVerifyRequest,
): Promise<CaptchaTokenResponse> {
  const response = await api.post<CaptchaTokenResponse>(
    "/auth/verify_captcha",
    request,
  );
  return response.data;
}

/**
 * Step 2.1: opens a Wordle session, spending one of the CAPTCHA token's
 * sessions.
 *
 * @param captchaToken - Token from verifyCaptcha
 * @returns The Wordle session ID
 */
export async function getWordleId(
  captchaToken: string,
): Promise<WordleResponse> {
  const response = await api.post<WordleResponse>(
    "/auth/wordle_id",
    undefined,
    {
      headers: {
        Authorization: `Bearer ${captchaToken}`,
      },
    },
  );
  return response.data;
}

/**
 * Step 2.2: submits one Wordle guess.
 *
 * @param captchaToken - Token from verifyCaptcha
 * @param request - Session ID and the guess
 * @returns The evaluation, guesses left and, once solved, the Wordle token
 */
export async function submitWordleGuess(
  captchaToken: string,
  request: WordleVerifyRequest,
): Promise<{
  evaluation: unknown;
  remaining_guesses: number;
  is_solution: boolean;
  wordle_token?: string;
  solution?: string;
}> {
  const response = await api.post("/auth/verify_wordle", request, {
    headers: {
      Authorization: `Bearer ${captchaToken}`,
    },
  });
  return response.data;
}

/**
 * Step 3: starts a Halli Galli game after latency calibration. Instructions
 * come from the game's public rules. Keep up to rules.max_preloaded_cards
 * future card images ready from initial_cards, then use
 * next_card_interval_ms after each reveal to schedule the next one. Spends
 * one of the Wordle token's games.
 *
 * @param wordleToken - Token from submitWordleGuess
 * @param calibrationId - ID of the finished latency calibration
 * @returns The game, its rules and the first card IDs
 */
export async function getHalliGalliGame(
  wordleToken: string,
  calibrationId: string,
): Promise<HalliGalliGameResponse> {
  const response = await api.post<HalliGalliGameResponse>(
    "/auth/halli_galli_id",
    { calibration_id: calibrationId },
    {
      headers: {
        Authorization: `Bearer ${wordleToken}`,
      },
    },
  );
  return response.data;
}

/**
 * Fetches the encrypted bytes of a card listed by the game's start response.
 * Each fetch rotates that round's key. If requests complete out of order,
 * keep the response with the highest version for the later reveal.
 *
 * @param gameId - Game from getHalliGalliGame
 * @param roundIndex - Round of the card
 * @returns The encrypted image and its version
 */
export async function getHalliGalliCard(
  gameId: string,
  roundIndex: number,
): Promise<{ image: Blob; imageVersion: number }> {
  const response = await api.post<Blob>(
    `/auth/halli_galli_card/${gameId}/${roundIndex}`,
    undefined,
    { responseType: "blob" },
  );
  return {
    image: response.data,
    imageVersion: Number(response.headers["x-halli-galli-image-version"]),
  };
}

/**
 * Reads the saved outcome and lives after a lost action response. A finished
 * player win also returns the same Halli Galli token saved with the game.
 *
 * @param gameId - Game from getHalliGalliGame
 * @returns The game's status
 */
export async function getHalliGalliStatus(
  gameId: string,
): Promise<HalliGalliStatusResponse> {
  const response = await api.get<HalliGalliStatusResponse>(
    `/auth/halli_galli_status/${gameId}`,
  );
  return response.data;
}

/**
 * Reveals a round. The key decrypts the image with the same image_version.
 * Reveal starts the server's round timer once; retrying it returns the same
 * key and version.
 *
 * @param gameId - Game from getHalliGalliGame
 * @param roundIndex - Round to reveal
 * @returns The decryption key and its image version
 */
export async function revealHalliGalliRound(
  gameId: string,
  roundIndex: number,
): Promise<HalliGalliRevealResponse> {
  const response = await api.post<HalliGalliRevealResponse>(
    `/auth/halli_galli_action/${gameId}/${roundIndex}`,
    { action: "reveal" },
  );
  return response.data;
}

/**
 * Buzzes on a round.
 *
 * @param gameId - Game from getHalliGalliGame
 * @param roundIndex - Current round
 * @param clickedCardId - Card that was clicked
 * @param clickX - Click position, normalized to the card's width
 * @param clickY - Click position, normalized to the card's height
 * @returns The round's outcome
 */
export async function buzzHalliGalliRound(
  gameId: string,
  roundIndex: number,
  clickedCardId: string,
  clickX: number,
  clickY: number,
): Promise<HalliGalliRoundResponse> {
  const response = await api.post<HalliGalliRoundResponse>(
    `/auth/halli_galli_action/${gameId}/${roundIndex}`,
    {
      action: "buzz",
      clicked_card_id: clickedCardId,
      click_x: clickX,
      click_y: clickY,
    },
  );
  return response.data;
}

/**
 * Moves on to the next round, only after the deadline. A missed winning
 * count loses a life.
 *
 * @param gameId - Game from getHalliGalliGame
 * @param roundIndex - Current round
 * @returns Lives, whether cards should clear, and the next IDs
 */
export async function nextHalliGalliRound(
  gameId: string,
  roundIndex: number,
): Promise<HalliGalliRoundResponse> {
  const response = await api.post<HalliGalliRoundResponse>(
    `/auth/halli_galli_action/${gameId}/${roundIndex}`,
    { action: "next" },
  );
  return response.data;
}

/**
 * Step 4: checks the security answers.
 *
 * @param halliGalliToken - Token of a won Halli Galli game
 * @param request - The answers
 * @returns The security token
 */
export async function verifySecurityQuestions(
  halliGalliToken: string,
  request: SecurityQuestionsRequest,
): Promise<SecurityTokenResponse> {
  const response = await api.post<SecurityTokenResponse>(
    "/auth/verify_security_questions",
    request,
    {
      headers: {
        Authorization: `Bearer ${halliGalliToken}`,
      },
    },
  );
  return response.data;
}

/**
 * Step 5: gets the player-removal token.
 *
 * @param securityToken - Token from verifySecurityQuestions
 * @returns The removal token
 */
export async function getRemovePlayerToken(
  securityToken: string,
): Promise<RemovePlayerTokenResponse> {
  const response = await api.post<RemovePlayerTokenResponse>(
    "/auth/remove_player_token",
    undefined,
    {
      headers: {
        Authorization: `Bearer ${securityToken}`,
      },
    },
  );
  return response.data;
}

/**
 * Sends the removal token with every following API request.
 *
 * @param token - Token from getRemovePlayerToken
 */
export function setRemovePlayerToken(token: string): void {
  api.defaults.headers.common["Authorization"] = `Bearer ${token}`;
}

/** Stops sending the removal token. */
export function clearRemovePlayerToken(): void {
  delete api.defaults.headers.common["Authorization"];
}
