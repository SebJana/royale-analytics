export type CaptchaResponse = {
  captcha_id: string;
};

export type CaptchaVerifyRequest = {
  captcha_id: string;
  answer: string;
};

export type CaptchaTokenResponse = {
  captcha_token: string;
};

export type WordleResponse = {
  wordle_id: string;
};

export type HalliGalliPublicRules = {
  visible_card_count: number;
  max_preloaded_cards: number;
  winning_fruit_count: number;
  winning_card_age: "oldest" | "newest";
  require_target_fruit: boolean;
  target_fruit_edge: "left" | "right" | "top" | "bottom";
};

export type HalliGalliGameResponse = {
  halli_galli_id: string;
  rules: HalliGalliPublicRules;
  next_card_interval_ms: number;
  initial_cards: { round_index: number; image_id: string }[];
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: HalliGalliGameStatus;
  halli_galli_token: string | null;
};

export type HalliGalliGameStatus = "playing" | "player_won" | "player_lost";
export type HalliGalliRoundResult =
  "player_won" | "player_lost" | "no_halli_galli";
export type HalliGalliRoundReason =
  | "correct_buzz"
  | "late_buzz"
  | "wrong_card"
  | "wrong_fruit"
  | "false_buzz"
  | "missed_halli_galli"
  | "no_halli_galli";

export type HalliGalliStatusResponse = {
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: HalliGalliGameStatus;
  halli_galli_token: string | null;
  current_image_id: string | null;
  prepared_cards: { round_index: number; image_id: string }[];
  last_round_index: number | null;
  last_round_result: HalliGalliRoundResult | null;
  last_round_reason: HalliGalliRoundReason | null;
  last_round_clear_cards: boolean | null;
  last_round_late_by_ms: number | null;
  last_round_winning_card_ids: string[];
};

export type HalliGalliRevealResponse = {
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: HalliGalliGameStatus;
  halli_galli_token: string | null;
  round_index: number;
  image_id: string;
  image_version: number;
  encryption_key: string;
};

export type HalliGalliRoundResponse = {
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: HalliGalliGameStatus;
  halli_galli_token: string | null;
  round_result: HalliGalliRoundResult;
  round_reason: HalliGalliRoundReason;
  late_by_ms: number | null;
  winning_card_ids: string[];
  clear_cards: boolean;
  next_card: { round_index: number; image_id: string } | null;
  preloaded_card: { round_index: number; image_id: string } | null;
};

export type WordleVerifyRequest = {
  wordle_id: string;
  wordle_guess: string;
  solution?: string;
};

export type SecurityQuestionsRequest = {
  most_annoying_card: string;
  most_skillful_card: string;
  most_mousey_card: string;
};

export type SecurityTokenResponse = {
  security_token: string;
};

export type RemovePlayerTokenResponse = {
  remove_player_token: string;
};

export type AuthState = {
  isAuthenticated: boolean;
  removePlayerToken?: string;
  expiresAt?: number;
};
