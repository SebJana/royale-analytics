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

// Step 1: Captcha
// Step 1.1: Get captcha ID
export async function getCaptchaId(): Promise<CaptchaResponse> {
  const response = await api.get<CaptchaResponse>("/auth/captcha_id");
  return response.data;
}

// Step 1.2: Get captcha image
export async function getCaptchaImage(captchaId: string): Promise<Blob> {
  const response = await api.get(`/auth/captcha_image/${captchaId}`, {
    responseType: "blob",
  });
  return response.data;
}

// Step 1.3: Verify captcha and get captcha token
export async function verifyCaptcha(
  request: CaptchaVerifyRequest,
): Promise<CaptchaTokenResponse> {
  const response = await api.post<CaptchaTokenResponse>(
    "/auth/verify_captcha",
    request,
  );
  return response.data;
}

// Step 2: Wordle
// Step 2.1: Get wordle challenge ID
export async function getWordleId(
  captchaToken: string,
): Promise<WordleResponse> {
  const response = await api.get<WordleResponse>("/auth/wordle_id", {
    headers: {
      Authorization: `Bearer ${captchaToken}`,
    },
  });
  return response.data;
}

// Step 2.2: Submit wordle guess and get evaluation/feedback
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

// Step 3: Halli Galli. Start it after latency calibration. Build instructions from this
// game's public rules. Keep up to rules.max_preloaded_cards future card images
// ready from initial_cards, then use next_card_interval_ms after each reveal
// to schedule the next one.
export async function getHalliGalliGame(
  wordleToken: string,
  calibrationId: string,
): Promise<HalliGalliGameResponse> {
  const response = await api.get<HalliGalliGameResponse>(
    "/auth/halli_galli_id",
    {
      headers: {
        Authorization: `Bearer ${wordleToken}`,
        "X-Halli-Galli-Calibration": calibrationId,
      },
    },
  );
  return response.data;
}

// Fetch the encrypted bytes for a card listed by this game's start response.
// Each fetch rotates that round's key. If requests complete out of order, keep
// the response with the highest version for the later reveal.
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

// Read the saved outcome and lives after a lost action response. A finished
// player win also returns the same Halli Galli token saved with the game.
export async function getHalliGalliStatus(
  gameId: string,
): Promise<HalliGalliStatusResponse> {
  const response = await api.get<HalliGalliStatusResponse>(
    `/auth/halli_galli_status/${gameId}`,
  );
  return response.data;
}

// The key decrypts the image with the same image_version. Reveal starts the
// server's round timer once; retrying it returns the same key and version.
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

// Send the clicked card ID and a point normalized to that card's dimensions.
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

// Move on only after the deadline. A missed winning count loses a life. A
// finished round returns lives, whether cards should clear, and the next IDs.
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

// Step 4: Verify security questions using the Halli Galli win token.
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

// Step 5: Get the final player-removal token
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

// Utility: Set player-removal token in axios headers
export function setRemovePlayerToken(token: string): void {
  api.defaults.headers.common["Authorization"] = `Bearer ${token}`;
}

// Utility: Clear player-removal token from axios headers
export function clearRemovePlayerToken(): void {
  delete api.defaults.headers.common["Authorization"];
}
