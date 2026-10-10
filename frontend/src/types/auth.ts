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

export type WordGuessResponse = {
  word_guess_id: string;
};

export type FruitBuzzPublicRules = {
  visible_card_count: number;
  max_preloaded_cards: number;
  winning_fruit_count: number;
  winning_card_age: "oldest" | "newest";
  require_target_fruit: boolean;
  target_fruit_edge: "left" | "right" | "top" | "bottom";
};

export type FruitBuzzGameResponse = {
  fruit_buzz_id: string;
  rules: FruitBuzzPublicRules;
  next_card_interval_ms: number;
  initial_cards: { round_index: number; image_id: string }[];
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: FruitBuzzGameStatus;
  fruit_buzz_token: string | null;
};

export type FruitBuzzGameStatus = "playing" | "player_won" | "player_lost";
export type FruitBuzzRoundResult =
  "player_won" | "player_lost" | "no_fruit_buzz";
export type FruitBuzzRoundReason =
  | "correct_buzz"
  | "late_buzz"
  | "wrong_card"
  | "wrong_fruit"
  | "false_buzz"
  | "missed_fruit_buzz"
  | "no_fruit_buzz";

export type FruitBuzzStatusResponse = {
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: FruitBuzzGameStatus;
  fruit_buzz_token: string | null;
  current_image_id: string | null;
  prepared_cards: { round_index: number; image_id: string }[];
  last_round_index: number | null;
  last_round_result: FruitBuzzRoundResult | null;
  last_round_reason: FruitBuzzRoundReason | null;
  last_round_clear_cards: boolean | null;
  last_round_late_by_ms: number | null;
  last_round_winning_card_ids: string[];
};

export type FruitBuzzRevealResponse = {
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: FruitBuzzGameStatus;
  fruit_buzz_token: string | null;
  round_index: number;
  image_id: string;
  image_version: number;
  encryption_key: string;
};

export type FruitBuzzRoundResponse = {
  current_round: number;
  player_lives: number;
  bot_lives: number;
  game_status: FruitBuzzGameStatus;
  fruit_buzz_token: string | null;
  round_result: FruitBuzzRoundResult;
  round_reason: FruitBuzzRoundReason;
  late_by_ms: number | null;
  winning_card_ids: string[];
  clear_cards: boolean;
  next_card: { round_index: number; image_id: string } | null;
  preloaded_card: { round_index: number; image_id: string } | null;
};

export type WordGuessVerifyRequest = {
  word_guess_id: string;
  guess: string;
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
