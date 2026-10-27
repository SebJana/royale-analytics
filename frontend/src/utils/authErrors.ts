import axios from "axios";

export type AuthErrorContext =
  | "captcha_load"
  | "captcha"
  | "word_guess_load"
  | "word_guess_submit"
  | "fruit_buzz"
  | "security"
  | "finish";

export type AuthErrorFeedback = {
  message: string;
  recovery?: "restart" | "word_guess";
  retryAt?: number;
};

/** An expected client-side challenge failure, including WebSocket failures. */
export class AuthChallengeError extends Error {
  readonly code: string;
  constructor(code: string) {
    super(code);
    this.name = "AuthChallengeError";
    this.code = code;
  }
}

// TODO [BUG] wrong message, if for example, Fruit Buzz token expires upon trying to submit security
// question answers, then it should say "took to long to answer questions/prove knowledge" and not
// "Fruit Buzz took too long. Restart verification."

const messages: Record<string, AuthErrorFeedback> = {
  CAPTCHA_INCORRECT: {
    message: "The CAPTCHA text doesn't match. Check the image and try again.",
  },
  CAPTCHA_EXPIRED: { message: "CAPTCHA took too long. Restart the CAPTCHA." },
  CAPTCHA_ATTEMPTS_EXHAUSTED: {
    message: "Too many wrong answers. Load a new CAPTCHA.",
  },
  CAPTCHA_TOKEN_EXPIRED: {
    message: "CAPTCHA took too long. Restart verification.",
    recovery: "restart",
  },
  CAPTCHA_TOKEN_USED_UP: {
    message: "This CAPTCHA can't start another Word Guess. Restart verification.",
    recovery: "restart",
  },
  WORD_GUESS_EXPIRED: {
    message: "Word Guess took too long. Restart Word Guess.",
    recovery: "word_guess",
  },
  WORD_GUESS_ATTEMPTS_EXHAUSTED: {
    message: "You've used all your guesses. Start a new Word Guess.",
    recovery: "word_guess",
  },
  WORD_GUESS_INVALID_WORD: {
    message: "That word isn't in the word list. Try another five-letter word.",
  },
  WORD_GUESS_TOKEN_EXPIRED: {
    message: "Fruit Buzz took too long. Restart verification from CAPTCHA.",
    recovery: "restart",
  },
  WORD_GUESS_TOKEN_USED_UP: {
    message:
      "This Word Guess can't start another Fruit Buzz game. Restart verification.",
    recovery: "restart",
  },
  FRUIT_BUZZ_TOKEN_EXPIRED: {
    message: "Fruit Buzz took too long. Restart verification.",
    recovery: "restart",
  },
  FRUIT_BUZZ_TOKEN_USED_UP: {
    message: "No answer attempts left. Restart verification.",
    recovery: "restart",
  },
  SECURITY_ANSWERS_INCORRECT: {
    message:
      "One or more answers are incorrect. Check all three answers and try again.",
  },
  SECURITY_TOKEN_EXPIRED: {
    message: "Verification took too long. Restart verification.",
    recovery: "restart",
  },
  SECURITY_TOKEN_USED_UP: {
    message: "This verification was already used. Restart verification.",
    recovery: "restart",
  },
  CALIBRATION_UNSTABLE: {
    message:
      "Your connection is too unstable for the timing challenge. Check your connection and try again.",
  },
  CALIBRATION_TIMEOUT: {
    message:
      "The connection check timed out. Check your connection and try again.",
  },
  CALIBRATION_CONNECTION: {
    message:
      "The connection check couldn't reach the server. Check your connection and try again.",
  },
  CALIBRATION_INVALID: {
    message: "The connection check didn't complete correctly. Try again.",
  },
  FRUIT_BUZZ_CARD_LOAD_FAILED: {
    message:
      "A game card couldn't load correctly. Start a new Fruit Buzz game.",
  },
  FRUIT_BUZZ_GAME_INCOMPLETE: {
    message:
      "The server couldn't prepare the next game card. Start a new Fruit Buzz game.",
  },
  FRUIT_BUZZ_WIN_UNAVAILABLE: {
    message:
      "Your win couldn't be verified. Please try a new Fruit Buzz game.",
  },
  // NOTE: Matches MediaPoolEmpty in backend/app/src/helpers/media_pool: the
  // server's CAPTCHA or card stock is empty until the media worker refills it.
  IMAGES_NOT_READY: {
    message: "Something went wrong. Please try again in a moment.",
  },
};

const fallbackMessages: Record<AuthErrorContext, string> = {
  captcha_load: "Couldn't load the CAPTCHA. Try loading it again.",
  captcha: "Couldn't verify the CAPTCHA. Please try again.",
  word_guess_load: "Couldn't load Word Guess. Try loading it again.",
  word_guess_submit: "Couldn't submit your guess. Please try again.",
  fruit_buzz: "Fruit Buzz couldn't continue. Start a new game.",
  security: "Couldn't check your answers. Please try again.",
  finish:
    "Your answers were accepted, but verification couldn't finish. Please try again.",
};

// Retry-After can be seconds or an HTTP date. Without a usable server value there
// is no known wait to enforce; a network timeout also doesn't imply a cooldown.
function retryDeadline(value: unknown): number | undefined {
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  if (typeof value === "string" && !value.trim()) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) {
    const deadline = Date.now() + seconds * 1000;
    return seconds >= 0 && Number.isFinite(deadline) ? deadline : undefined;
  }
  const date = Date.parse(String(value));
  return Number.isFinite(date) ? Math.max(Date.now(), date) : undefined;
}

/** Show known, actionable failures rather than raw HTTP or internal error text. */
export function getAuthErrorFeedback(
  error: unknown,
  context: AuthErrorContext,
): AuthErrorFeedback {
  if (error instanceof AuthChallengeError) {
    return messages[error.code] ?? { message: fallbackMessages[context] };
  }
  if (!axios.isAxiosError(error)) return { message: fallbackMessages[context] };
  if (!error.response) {
    return {
      message:
        error.code === "ECONNABORTED" || error.code === "ETIMEDOUT"
          ? "The request timed out. Check your connection and try again."
          : "Couldn't reach the server. Check your connection and try again.",
    };
  }

  const { status, data, headers } = error.response;
  const detail = data?.detail;
  const code =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? detail.code
      : undefined;
  // An empty image stock is a short wait, so the retry button honors Retry-After.
  if (code === "IMAGES_NOT_READY") {
    return {
      ...messages.IMAGES_NOT_READY,
      retryAt: retryDeadline(headers?.["retry-after"]),
    };
  }
  // A used-up Word Guess is also HTTP 429, but it needs a new game, not a cooldown.
  if (typeof code === "string" && messages[code]) return messages[code];
  if (status === 429) {
    if (
      typeof detail === "string" &&
      detail.startsWith("Maximum amount of guesses reached")
    ) {
      return messages.WORD_GUESS_ATTEMPTS_EXHAUSTED;
    }
    const attempts =
      context === "security" ? "Too many attempts" : "Too many requests";
    const retryAt = retryDeadline(headers?.["retry-after"]);
    return {
      message:
        retryAt === undefined
          ? `${attempts}. Please try again shortly.`
          : `${attempts}.`,
      retryAt,
    };
  }
  if (status >= 500)
    return {
      message: `${fallbackMessages[context]} The server is having trouble.`,
    };
  if (status === 401 || status === 403) {
    if (context === "captcha") return messages.CAPTCHA_INCORRECT;
    if (context === "security") {
      return typeof detail === "string" && detail.includes("incorrect answers")
        ? messages.SECURITY_ANSWERS_INCORRECT
        : messages.FRUIT_BUZZ_TOKEN_EXPIRED;
    }
    if (context === "fruit_buzz") {
      return typeof detail === "string" &&
        detail.toLowerCase().includes("calibration")
        ? messages.CALIBRATION_INVALID
        : messages.WORD_GUESS_TOKEN_EXPIRED;
    }
    if (context === "finish") return messages.SECURITY_TOKEN_EXPIRED;
    return messages.CAPTCHA_TOKEN_EXPIRED;
  }
  if (status === 404) {
    if (context === "captcha" || context === "captcha_load")
      return messages.CAPTCHA_EXPIRED;
    if (context === "word_guess_submit") return messages.WORD_GUESS_EXPIRED;
    if (context === "fruit_buzz")
      return { message: "Fruit Buzz took too long. Restart Fruit Buzz." };
  }
  if (status === 422) {
    if (context === "word_guess_submit" && typeof detail === "string")
      return messages.WORD_GUESS_INVALID_WORD;
    return {
      message:
        context === "security"
          ? "Please enter an answer for all three questions."
          : "The challenge request wasn't accepted. Check your entry and try again.",
    };
  }
  if (status === 409 && context === "fruit_buzz") {
    return {
      message: "The game got out of sync. Start a new Fruit Buzz game.",
    };
  }
  return { message: fallbackMessages[context] };
}
