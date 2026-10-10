import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
} from "react";
import { Button, CircularProgress } from "@mui/material";
import {
  Bot,
  Cherry,
  ChevronUp,
  Citrus,
  Gauge,
  Grape,
  Heart,
  Pause,
  Play,
  UserRound,
  Zap,
} from "lucide-react";
import { jwtDecode } from "jwt-decode";
import axios from "axios";
import {
  AuthChallengeError,
  getAuthErrorFeedback,
  type AuthErrorFeedback,
} from "../../utils/authErrors";
import {
  buzzFruitBuzzRound,
  getFruitBuzzCard,
  getFruitBuzzGame,
  getFruitBuzzStatus,
  nextFruitBuzzRound,
  revealFruitBuzzRound,
} from "../../services/api/auth";
import type {
  FruitBuzzGameResponse,
  FruitBuzzPublicRules,
  FruitBuzzRoundReason,
  FruitBuzzRoundResponse,
} from "../../types/auth";
import {
  calibrateFruitBuzz,
  decryptFruitBuzzCard,
  matchingCard,
  type EncryptedCard,
} from "./fruitBuzzProtocol";
import {
  addVisibleCard,
  afterRound,
  createSettlementGate,
  recoverSettledRound,
  scheduleNextCard,
  type VisibleCard,
} from "./fruitBuzzFlow";
import "./fruitBuzz.css";
import { AuthActionButton } from "./authActionButton";
import { useAuthCooldown } from "../../hooks/useAuthCooldown";

type Phase =
  | "loading"
  | "ready"
  | "starting"
  | "playing"
  | "settling"
  | "advancing"
  | "animating"
  | "round_ready"
  | "won"
  | "lost"
  | "error";
const ROUND_FEEDBACK_MS = 2200;
const CARD_ASPECT_RATIO = 5 / 7;
// Full rules are shown only if the maximum card count still fits at this width.
// Desktop width alone doesn't mean there's enough height for readable cards.
const DESKTOP_RULES_MIN_CARD_WIDTH = 200;
// Only used to measure the hidden loading copy before the backend sends rules.
// Keep these representative values in sync with the current game configuration.
const LOADING_RULES: FruitBuzzPublicRules = {
  winning_fruit_count: 5,
  visible_card_count: 4,
  max_preloaded_cards: 3,
  winning_card_age: "oldest",
  require_target_fruit: true,
  target_fruit_edge: "right",
};

const feedbackText: Record<FruitBuzzRoundReason, string> = {
  correct_buzz: "Fruit Buzz! The bot loses a life.",
  late_buzz: "Too late!",
  wrong_card: "That was not the right card. You lost a life.",
  wrong_fruit: "That was not the target fruit. You lost a life.",
  false_buzz: "No Fruit Buzz yet. You lost a life.",
  missed_fruit_buzz: "The bot got there first. You lost a life.",
  no_fruit_buzz: "No Fruit Buzz. Next card!",
};

interface FruitBuzzProps {
  readonly wordGuessToken: string;
  readonly onWin: (token: string) => void;
  readonly onWordGuessExpired: () => void;
}

/** Avoid opening a new connection for a token whose local expiry has passed. */
function tokenExpired(token: string): boolean {
  try {
    const { exp } = jwtDecode<{ exp?: number }>(token);
    return !exp || exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

/** Run one Fruit Buzz attempt inside the existing authentication modal. */
export function FruitBuzz({
  wordGuessToken,
  onWin,
  onWordGuessExpired,
}: FruitBuzzProps) {
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState<Phase>("loading");
  const [loadingStage, setLoadingStage] = useState<"calibrating" | "preparing">(
    "calibrating",
  );
  const [game, setGame] = useState<FruitBuzzGameResponse | null>(null);
  const [pile, setPile] = useState<VisibleCard[]>([]);
  const pileRef = useRef<HTMLDivElement>(null);
  const desktopRulesRef = useRef<HTMLDivElement>(null);
  const [showDesktopRules, setShowDesktopRules] = useState(false);
  const [pileLayout, setPileLayout] = useState({ columns: 1, rows: 1 });
  const showingGame = game !== null;
  const rules = game?.rules ?? LOADING_RULES;
  const configuredCardCount = rules.visible_card_count;
  const [playerLives, setPlayerLives] = useState(0);
  const [botLives, setBotLives] = useState(0);
  const [progress, setProgress] = useState(0);
  const [feedback, setFeedback] = useState<FruitBuzzRoundReason | null>(null);
  const [lateByMs, setLateByMs] = useState<number | null>(null);
  const [winningCardIds, setWinningCardIds] = useState<string[]>([]);
  const [clickedCardId, setClickedCardId] = useState<string | null>(null);
  const [winToken, setWinToken] = useState<string | null>(null);
  const [pauseAfterRound, setPauseAfterRound] = useState(false);
  const [gameEnded, setGameEnded] = useState(false);
  const [error, setError] = useState<AuthErrorFeedback | null>(null);
  const cooldown = useAuthCooldown(error);
  const pauseAfterRoundRef = useRef(false);
  const buzzRef = useRef<(card: VisibleCard, x: number, y: number) => void>(
    () => {},
  );
  const beginRef = useRef<() => void>(() => {});
  // Parent callbacks can change while the game is open. Read their latest
  // values without restarting calibration or discarding preloaded cards.
  const callbacksRef = useRef({ onWin, onWordGuessExpired });
  useEffect(() => {
    callbacksRef.current = { onWin, onWordGuessExpired };
  }, [onWin, onWordGuessExpired]);

  // Reserve the full configured window so cards do not resize as it fills.
  // Empty slots stay empty; temporary card placeholders would disappear again
  // as soon as the first card is revealed and make the round start look inconsistent.
  useEffect(() => {
    const element = pileRef.current;
    if (!element || !showingGame) return;
    const count = configuredCardCount;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const gap = parseFloat(getComputedStyle(element).gap) || 0;
      let best = { columns: 1, rows: count };
      let largestWidth = 0;
      for (let columns = 1; columns <= count; columns++) {
        const rows = Math.ceil(count / columns);
        const cardWidth = Math.min(
          (width - gap * (columns - 1)) / columns,
          ((height - gap * (rows - 1)) / rows) * CARD_ASPECT_RATIO,
        );
        if (cardWidth > largestWidth) {
          largestWidth = cardWidth;
          best = { columns, rows };
        }
      }
      setPileLayout((previous) =>
        previous.columns === best.columns && previous.rows === best.rows
          ? previous
          : best,
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [configuredCardCount, showingGame]);

  // Show full desktop instructions when they leave room for the entire pile
  // at a readable card width. Reserve the timer even before play starts.
  useLayoutEffect(() => {
    const pileElement = pileRef.current;
    const rules = desktopRulesRef.current;
    const panel = pileElement?.parentElement;
    const content = panel?.parentElement;
    const heading = panel?.querySelector<HTMLElement>(".auth-stage-heading");
    const lives = panel?.querySelector<HTMLElement>(".buzz-lives");
    if (!pileElement || !rules || !panel || !content || !heading || !lives)
      return;

    const update = () => {
      const panelStyle = getComputedStyle(panel);
      const contentStyle = getComputedStyle(content);
      const gap = parseFloat(panelStyle.gap) || 0;
      const cardGap = parseFloat(getComputedStyle(pileElement).gap) || 0;
      const width =
        panel.clientWidth -
        parseFloat(panelStyle.paddingLeft) -
        parseFloat(panelStyle.paddingRight);
      const columns = Math.min(
        configuredCardCount,
        Math.max(
          1,
          Math.floor(
            (width + cardGap) / (DESKTOP_RULES_MIN_CARD_WIDTH + cardGap),
          ),
        ),
      );
      const rows = Math.ceil(configuredCardCount / columns);
      const minimumPileHeight =
        (rows * DESKTOP_RULES_MIN_CARD_WIDTH) / CARD_ASPECT_RATIO +
        (rows - 1) * cardGap;
      const availableHeight =
        content.clientHeight -
        parseFloat(contentStyle.paddingTop) -
        parseFloat(contentStyle.paddingBottom) -
        parseFloat(panelStyle.paddingTop) -
        parseFloat(panelStyle.paddingBottom) -
        2 -
        heading.offsetHeight -
        lives.offsetHeight -
        44 -
        4 * gap;
      setShowDesktopRules(
        window.matchMedia("(min-width: 769px)").matches &&
          rules.offsetHeight + minimumPileHeight <= availableHeight,
      );
    };
    // Decide before paint, otherwise the full rules can appear a frame later
    // and shift the lives and cards while the player is looking at them.
    update();
    const observer = new ResizeObserver(update);
    observer.observe(content);
    observer.observe(rules);
    observer.observe(heading);
    observer.observe(lives);
    return () => observer.disconnect();
  }, [configuredCardCount, showingGame]);

  useEffect(() => {
    // This attempt owns its socket, timers, ciphertext, and object URLs. Effect
    // cleanup prevents a closed modal or old retry from updating the new game.
    let active = true;
    const controller = new AbortController();
    const ciphertext = new Map<number, EncryptedCard>();
    const pendingPreloads = new Map<number, Promise<void>>();
    const urls = new Set<string>();
    let visibleCards: VisibleCard[] = [];
    let timer: number | undefined;
    let barTimer: number | undefined;
    let feedbackTimer: number | undefined;
    let cancelNext = () => {};
    let currentRound = -1;
    let roundDue = 0;
    const settlementGate = createSettlementGate();
    let gameId = "";
    let gameData: FruitBuzzGameResponse;

    /** The bar measures the next-card interval, never the hidden buzz deadline. */
    function progressAt(due: number) {
      return Math.max(
        0,
        Math.min(
          100,
          100 *
            (1 - (due - performance.now()) / gameData.next_card_interval_ms),
        ),
      );
    }
    /** Stop both the next action and the bar when a round or attempt ends. */
    function clearTimer() {
      cancelNext();
      window.clearTimeout(timer);
      window.clearInterval(barTimer);
      window.clearTimeout(feedbackTimer);
    }
    function clearVisiblePile() {
      const oldPile = visibleCards;
      visibleCards = afterRound(visibleCards, true);
      setPile(visibleCards);
      for (const old of oldPile) {
        urls.delete(old.url);
        window.setTimeout(() => URL.revokeObjectURL(old.url), 0);
      }
    }
    /** Separate an expired Word Guess token from retryable game or network errors. */
    function fail(err: unknown) {
      if (!active) return;
      clearTimer();
      setPhase("error");
      setError(
        getAuthErrorFeedback(
          tokenExpired(wordGuessToken)
            ? new AuthChallengeError("WORD_GUESS_TOKEN_EXPIRED")
            : err,
          "fruit_buzz",
        ),
      );
    }
    /** Wait for the next-card cadence or result feedback to finish. */
    function waitUntil(when: number): Promise<void> {
      return new Promise((resolve) => {
        timer = window.setTimeout(
          resolve,
          Math.max(0, when - performance.now()),
        );
      });
    }
    /** Fetch one encrypted version per round; a second POST rotates its key. */
    async function preload(index: number, imageId: string) {
      if (ciphertext.has(index)) return;
      if (pendingPreloads.has(index)) return pendingPreloads.get(index);
      // Concurrent callers share this in-flight request instead of rotating the
      // key twice and leaving an old ciphertext beside a newer reveal key.
      const request = getFruitBuzzCard(gameId, index)
        .then((result) => {
          if (active) ciphertext.set(index, { ...result, imageId });
        })
        .finally(() => pendingPreloads.delete(index));
      pendingPreloads.set(index, request);
      return request;
    }
    /** Reveal the matching preloaded image and start this round's UI clock. */
    async function reveal(index: number, imageId: string) {
      await preload(index, imageId);
      if (!active) return;
      let response;
      try {
        response = await revealFruitBuzzRound(gameId, index);
      } catch (requestError) {
        // Reveal retries return the original key and deadline. Recover a lost
        // reply only while status still points at this same current round.
        const status = await getFruitBuzzStatus(gameId);
        if (status.current_round !== index || status.game_status !== "playing")
          throw requestError;
        response = await revealFruitBuzzRound(gameId, index);
      }
      // The server starts its clock on reveal. Start the next-card bar only
      // after decryption, when the card can actually be shown.
      const card = ciphertext.get(index);
      if (!card || !matchingCard(card, response)) {
        throw new AuthChallengeError("FRUIT_BUZZ_CARD_LOAD_FAILED");
      }
      const url = await decryptFruitBuzzCard(card, response.encryption_key);
      if (!active) {
        URL.revokeObjectURL(url);
        return;
      }
      urls.add(url);
      window.clearTimeout(feedbackTimer);
      ciphertext.delete(index);
      currentRound = index;
      settlementGate.open(index);
      window.clearInterval(barTimer);
      // Revoke cards that fell outside the backend's visible-card window. The
      // timeout gives React a chance to remove their images from the screen.
      const nextPile = addVisibleCard(
        visibleCards,
        { roundIndex: index, imageId, url },
        gameData.rules.visible_card_count,
      );
      for (const old of visibleCards) {
        if (!nextPile.includes(old)) {
          urls.delete(old.url);
          window.setTimeout(() => URL.revokeObjectURL(old.url), 0);
        }
      }
      visibleCards = nextPile;
      setPile(nextPile);
      setFeedback(null);
      setLateByMs(null);
      setWinningCardIds([]);
      setClickedCardId(null);
      pauseAfterRoundRef.current = false;
      setPauseAfterRound(false);
      setGameEnded(false);
      roundDue = performance.now() + gameData.next_card_interval_ms;
      setPhase("playing");
      setProgress(0);
      barTimer = window.setInterval(() => {
        setProgress(progressAt(roundDue));
      }, 50);
      cancelNext = scheduleNextCard(roundDue, () => {
        void settle(index);
      });
    }
    /** Send one buzz or timed advance, then use saved status if its reply is lost. */
    async function settle(
      index: number,
      buzz?: { imageId: string; x: number; y: number },
    ) {
      if (!active || !settlementGate.claim(index)) return;
      // Freeze the bar at the buzz point and cancel the automatic action.
      // A timed advance has already reached the end of the bar.
      if (buzz) {
        cancelNext();
        window.clearInterval(barTimer);
        setProgress(progressAt(roundDue));
      } else {
        clearTimer();
        setProgress(100);
      }
      setPhase(buzz ? "settling" : "advancing");
      let result: FruitBuzzRoundResponse;
      try {
        try {
          result = buzz
            ? await buzzFruitBuzzRound(
                gameId,
                index,
                buzz.imageId,
                buzz.x,
                buzz.y,
              )
            : await nextFruitBuzzRound(gameId, index);
        } catch (requestError) {
          // The request may have committed even if its response was lost. Read
          // status before considering another action for the same round.
          const status = await getFruitBuzzStatus(gameId);
          if (
            status.current_round === index &&
            !buzz &&
            axios.isAxiosError(requestError) &&
            requestError.response?.status === 409
          ) {
            // Every round rejects an early next, including ordinary rounds.
            // Wait briefly instead of treating 409 as a clue about the cards.
            settlementGate.release(index);
            timer = window.setTimeout(() => {
              void settle(index);
            }, 150);
            return;
          }
          const recovered = recoverSettledRound(status, index);
          if (!recovered) throw requestError;
          result = recovered;
        }
        if (!active) return;
        setPlayerLives(result.player_lives);
        setBotLives(result.bot_lives);
        setFeedback(result.round_reason);
        setLateByMs(result.late_by_ms);
        setWinningCardIds(result.winning_card_ids);
        setClickedCardId(buzz?.imageId ?? null);
        setGameEnded(result.game_status !== "playing");
        const scored = result.round_result !== "no_fruit_buzz";
        const feedbackDue = performance.now() + ROUND_FEEDBACK_MS;
        if (scored) {
          // Keep the settled cards in place while their answer is highlighted.
          // Clear after feedback even if preparation takes longer or the
          // player has chosen to pause before the next reveal.
          setPhase("animating");
          feedbackTimer = window.setTimeout(() => {
            if (
              active &&
              result.clear_cards &&
              result.game_status === "playing"
            ) {
              clearVisiblePile();
              setPhase((previous) =>
                previous === "animating" ? "advancing" : previous,
              );
            }
          }, ROUND_FEEDBACK_MS);
        }
        if (result.game_status === "player_won") {
          if (result.fruit_buzz_token) {
            if (scored) await waitUntil(feedbackDue);
            if (!active) return;
            setWinToken(result.fruit_buzz_token);
            setPhase("won");
          } else throw new AuthChallengeError("FRUIT_BUZZ_WIN_UNAVAILABLE");
          return;
        }
        if (result.game_status === "player_lost") {
          if (scored) await waitUntil(feedbackDue);
          if (!active) return;
          setPhase("lost");
          return;
        }
        const next = result.next_card;
        if (!next) throw new AuthChallengeError("FRUIT_BUZZ_GAME_INCOMPLETE");
        // Status recovery names the far end too, so both paths refill once.
        const future = result.preloaded_card ? [result.preloaded_card] : [];
        if (scored) {
          // Keep the scored board visible for feedback while preparing the next
          // window, and preserve the minimum card cadence.
          await preload(next.round_index, next.image_id);
          for (const card of future) {
            if (card.round_index > next.round_index)
              await preload(card.round_index, card.image_id);
          }
          await waitUntil(Math.max(roundDue, feedbackDue));
          if (!active) return;
        }
        if (pauseAfterRoundRef.current) {
          // The pause request only takes effect after the current round has
          // settled, before the next card is revealed.
          if (!scored) {
            await preload(next.round_index, next.image_id);
            for (const card of future) {
              if (card.round_index > next.round_index)
                await preload(card.round_index, card.image_id);
            }
          }
          if (!active) return;
          let resumed = false;
          pauseAfterRoundRef.current = false;
          setPauseAfterRound(false);
          beginRef.current = () => {
            if (resumed || !active) return;
            resumed = true;
            window.clearTimeout(feedbackTimer);
            if (result.clear_cards) clearVisiblePile();
            setPhase("starting");
            void reveal(next.round_index, next.image_id).catch(fail);
          };
          setPhase("round_ready");
          return;
        }
        if (result.clear_cards) clearVisiblePile();
        if (active) {
          setPhase("advancing");
          await reveal(next.round_index, next.image_id);
        }
        // On an ordinary round the next image is already cached. Reveal it
        // before fetching the far future image so the cadence stays smooth.
        if (!buzz) {
          for (const card of future) {
            if (card.round_index > next.round_index)
              await preload(card.round_index, card.image_id);
          }
        }
      } catch (err) {
        fail(err);
      }
    }
    buzzRef.current = (card, x, y) => {
      void settle(currentRound, { imageId: card.imageId, x, y });
    };

    /** Calibrate, create the game, and fill its initial window before play. */
    async function start() {
      // Rules belong to one attempt. Hide the previous game's instructions
      // while calibration and game creation fetch the new rules.
      setGame(null);
      try {
        if (tokenExpired(wordGuessToken)) {
          fail(new AuthChallengeError("WORD_GUESS_TOKEN_EXPIRED"));
          return;
        }
        setPhase("loading");
        setLoadingStage("calibrating");
        setError(null);
        setPile([]);
        setFeedback(null);
        setLateByMs(null);
        setWinningCardIds([]);
        setClickedCardId(null);
        pauseAfterRoundRef.current = false;
        setPauseAfterRound(false);
        setGameEnded(false);
        setWinToken(null);
        const calibrationId = await calibrateFruitBuzz(
          wordGuessToken,
          controller.signal,
        );
        if (!active) return;
        setLoadingStage("preparing");
        gameData = await getFruitBuzzGame(wordGuessToken, calibrationId);
        if (!active) return;
        gameId = gameData.fruit_buzz_id;
        setGame(gameData);
        setPlayerLives(gameData.player_lives);
        setBotLives(gameData.bot_lives);
        // Fetch serially because every preload updates the same Redis game.
        for (const card of gameData.initial_cards) {
          await preload(card.round_index, card.image_id);
        }
        if (!active) return;
        const first = gameData.initial_cards.find(
          (card) => card.round_index === gameData.current_round,
        );
        if (!first) throw new AuthChallengeError("FRUIT_BUZZ_GAME_INCOMPLETE");
        // Hold the first reveal until the player has read this game's rules.
        // The server's reaction clock starts only when reveal is requested.
        let started = false;
        beginRef.current = () => {
          if (started || !active) return;
          started = true;
          setPhase("starting");
          void reveal(first.round_index, first.image_id).catch(fail);
        };
        setPhase("ready");
      } catch (err) {
        fail(err);
      }
    }
    void start();
    return () => {
      active = false;
      beginRef.current = () => {};
      controller.abort();
      clearTimer();
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [attempt, wordGuessToken]);

  function handleCardClick(
    event: MouseEvent<HTMLButtonElement>,
    card: VisibleCard,
  ) {
    if (phase !== "playing") return;
    const image = event.currentTarget.querySelector("img");
    if (!image) return;
    const rect = image.getBoundingClientRect();
    // The image fills its own button without cropping, so this point matches
    // the server's normalized PNG coordinates on mouse and touch screens.
    const x = Math.max(
      0,
      Math.min(1, (event.clientX - rect.left) / rect.width),
    );
    const y = Math.max(
      0,
      Math.min(1, (event.clientY - rect.top) / rect.height),
    );
    buzzRef.current(card, x, y);
  }

  const messageTone =
    phase === "won" || (phase === "animating" && feedback === "correct_buzz")
      ? "good"
      : phase === "lost" ||
          (phase === "animating" && feedback && feedback !== "no_fruit_buzz")
        ? "bad"
        : "neutral";
  const showingResultCards =
    phase === "animating" ||
    phase === "round_ready" ||
    phase === "won" ||
    phase === "lost";
  const timerIdle =
    phase === "ready" ||
    phase === "starting" ||
    phase === "round_ready" ||
    phase === "won" ||
    phase === "lost" ||
    phase === "error";
  const pauseLabel = pauseAfterRound
    ? "Cancel scheduled pause"
    : "Pause after this round";
  const feedbackMessage = feedback ? feedbackText[feedback] : null;
  const lateBuzzDetail =
    feedback === "late_buzz"
      ? `${lateByMs === null ? "Your buzz missed the deadline." : `${lateByMs} ms past the deadline.`} ${gameEnded ? "That was your last life." : "You lost a life."}`
      : null;
  const lateBuzzIcon =
    feedback === "late_buzz" ? (
      <Gauge className="buzz-late-icon" size={38} aria-hidden="true" />
    ) : null;
  const ruleText = (
    <>
      <p>
        Buzz when one fruit totals exactly{" "}
        <strong>{rules.winning_fruit_count}</strong> across the visible cards.
        Every visible card counts, including all{" "}
        <strong>{rules.visible_card_count}</strong> once they are shown.
      </p>
      <p>
        Count only the <strong>actual fruit emojis</strong>. Ignore colored
        blobs, noise, and all other distractions.
      </p>
      <p className="buzz-click-rule">
        Click the <strong>{rules.winning_card_age}</strong> card of the{" "}
        <strong>winning fruit</strong>.{" "}
        {rules.require_target_fruit ? (
          <>
            Click the <strong>{rules.target_fruit_edge}most</strong> fruit on
            that card.
          </>
        ) : (
          <>
            Click <strong>anywhere</strong> on that card.
          </>
        )}
      </p>
      <p>
        Be quicker than the bot: a correct buzz costs it one life. If it gets
        there first, you lose one. An incorrect buzz also costs you one life.
        The bar is a guide to the next card; the bot can beat you before it
        fills.
      </p>
    </>
  );

  const loadingIndicator = (
    <div className={`buzz-loading is-${loadingStage}`} role="status">
      <div className={`buzz-loading-visual is-${loadingStage}`}>
        {loadingStage === "calibrating" && (
          <div className="buzz-loading-fruits" aria-hidden="true">
            <Cherry size={30} />
            <Citrus size={30} />
            <Grape size={30} />
          </div>
        )}
        <span className="buzz-loading-core">
          <CircularProgress
            className="buzz-loading-spinner"
            size={40}
            aria-label="Loading Fruit Buzz"
          />
        </span>
      </div>
      <div className="buzz-loading-copy">
        <strong>
          {loadingStage === "calibrating"
            ? "Checking connectivity…"
            : "Preparing cards…"}
        </strong>
      </div>
      <div className="buzz-loading-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );

  return (
    <div
      className={`fruit-buzz auth-step phase-${phase}`}
      aria-busy={phase === "loading"}
    >
      <h3 className="auth-stage-heading">
        Play Fruit Buzz to prove your reaction speed
      </h3>
      <div
        className={`buzz-desktop-rules${showDesktopRules ? " is-visible" : ""}`}
        aria-hidden={!game || !showDesktopRules}
      >
        <div
          className={`buzz-rules${!game ? " is-loading" : ""}`}
          ref={desktopRulesRef}
        >
          {game ? (
            ruleText
          ) : (
            <>
              <div className="buzz-rules-measure">{ruleText}</div>
              <div className="buzz-rules-blocks">
                <span className="auth-skeleton" />
                <span className="auth-skeleton is-highlighted" />
                <span className="auth-skeleton" />
              </div>
            </>
          )}
        </div>
      </div>
      {!game && (
        <>
          {!showDesktopRules && (
            <div className="buzz-rules-placeholder" aria-hidden="true">
              <span className="auth-skeleton" />
              <span className="auth-skeleton" />
            </div>
          )}
          <div className="buzz-lives" aria-hidden="true">
            <div>
              <span className="buzz-life-label">
                <UserRound size={21} /> You
              </span>
              <span className="auth-skeleton buzz-lives-placeholder" />
            </div>
            <div>
              <span className="auth-skeleton buzz-lives-placeholder" />
              <span className="buzz-life-label">
                Bot <Bot size={21} />
              </span>
            </div>
          </div>
          <div className="buzz-timer-row" aria-hidden="true">
            <div className="buzz-pause-slot" />
          </div>
          <div className="buzz-pile buzz-placeholder-pile" ref={pileRef}>
            {phase === "loading" && loadingIndicator}
          </div>
        </>
      )}
      {game && (
        <>
          {!showDesktopRules && (
            <details className="buzz-mobile-rules">
              <summary>
                <span>
                  Rules: Buzz at {game.rules.winning_fruit_count} ·{" "}
                  {game.rules.winning_card_age} ·{" "}
                  {game.rules.require_target_fruit
                    ? `${game.rules.target_fruit_edge}most`
                    : "anywhere"}
                </span>
                <ChevronUp
                  className="buzz-rules-toggle"
                  size={18}
                  aria-hidden="true"
                />
              </summary>
              <div className="buzz-rules">{ruleText}</div>
            </details>
          )}
          <div className="buzz-lives" aria-label="Remaining lives">
            <div>
              <span className="buzz-life-label">
                <UserRound size={21} aria-hidden="true" /> <span>You</span>
              </span>
              <span aria-label={`${playerLives} lives left`}>
                {Array.from({ length: game.player_lives }, (_, i) => (
                  <Heart
                    key={i}
                    size={21}
                    fill={i < playerLives ? "currentColor" : "none"}
                    className={i < playerLives ? "life-active" : "life-empty"}
                  />
                ))}
              </span>
            </div>
            <div>
              <span aria-label={`${botLives} lives left`}>
                {Array.from({ length: game.bot_lives }, (_, i) => (
                  <Heart
                    key={i}
                    size={21}
                    fill={i < botLives ? "currentColor" : "none"}
                    className={i < botLives ? "life-active" : "life-empty"}
                  />
                ))}
              </span>
              <span className="buzz-life-label">
                <span>Bot</span> <Bot size={21} aria-hidden="true" />
              </span>
            </div>
          </div>
          <div className="buzz-timer-row">
            <span
              className={`buzz-timer-label ${timerIdle ? "is-idle" : ""}`}
              aria-hidden={timerIdle}
            >
              Next card
            </span>
            <progress
              className={`buzz-progress ${timerIdle ? "is-idle" : ""}`}
              aria-label="Next card interval"
              value={Math.round(progress)}
              max={100}
            />
            <div className="buzz-pause-slot">
              {(phase === "playing" ||
                phase === "settling" ||
                phase === "advancing" ||
                (phase === "animating" && !gameEnded)) && (
                <Button
                  className="buzz-pause-button"
                  variant={pauseAfterRound ? "contained" : "outlined"}
                  aria-label={pauseLabel}
                  title={pauseLabel}
                  aria-pressed={pauseAfterRound}
                  onClick={() => {
                    pauseAfterRoundRef.current = !pauseAfterRoundRef.current;
                    setPauseAfterRound(pauseAfterRoundRef.current);
                  }}
                >
                  {pauseAfterRound ? (
                    <Play size={20} fill="currentColor" aria-hidden="true" />
                  ) : (
                    <Pause size={20} fill="currentColor" aria-hidden="true" />
                  )}
                </Button>
              )}
            </div>
          </div>
          <div
            className="buzz-pile"
            ref={pileRef}
            style={
              {
                "--pile-columns": pileLayout.columns,
                "--pile-rows": pileLayout.rows,
              } as CSSProperties
            }
            // TODO keep sliding window for cards or have the n + 1 card replace the oldest
            // current card so that all cards keep their position until they're replaced
          >
            {phase === "loading" && loadingIndicator}
            {phase === "animating" && (
              <div
                className={`buzz-result-message ${messageTone} ${feedback === "correct_buzz" ? "is-correct" : ""} ${feedback === "missed_fruit_buzz" ? "is-missed" : ""}`}
                role="status"
              >
                {(feedback === "correct_buzz" ||
                  feedback === "missed_fruit_buzz") && (
                  <span className="buzz-result-impact" aria-hidden="true">
                    <span className="buzz-impact-ring" />
                    <span className="buzz-impact-rays" />
                    {feedback === "correct_buzz" ? (
                      <Zap size={24} fill="currentColor" />
                    ) : (
                      <Bot size={24} />
                    )}
                  </span>
                )}
                {lateBuzzIcon}
                <strong>{feedbackMessage}</strong>
                {lateBuzzDetail && <span>{lateBuzzDetail}</span>}
              </div>
            )}
            {pile.map((card) => (
              <button
                type="button"
                key={card.roundIndex}
                className={[
                  "buzz-card",
                  showingResultCards && winningCardIds.includes(card.imageId)
                    ? feedback === "correct_buzz"
                      ? "is-winning player-win"
                      : feedback === "missed_fruit_buzz"
                        ? "is-winning bot-win bot-steal"
                        : "is-winning bot-win"
                    : "",
                  showingResultCards &&
                  clickedCardId === card.imageId &&
                  (feedback === "wrong_card" || feedback === "false_buzz")
                    ? "is-wrong"
                    : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                disabled={phase !== "playing"}
                onClick={(event) => handleCardClick(event, card)}
                aria-label={`Card ${card.roundIndex + 1}; ${game.rules.require_target_fruit ? "click a fruit" : "click the card"} to buzz`}
              >
                <img
                  src={card.url}
                  alt={`Fruit card ${card.roundIndex + 1}`}
                  draggable={false}
                />
              </button>
            ))}
            {(phase === "starting" ||
              phase === "settling" ||
              phase === "advancing") && (
              <div
                className="buzz-checking-status"
                role="status"
                aria-label={
                  phase === "settling"
                    ? "Checking your buzz"
                    : "Preparing the next card"
                }
              >
                <CircularProgress
                  size={22}
                  className="buzz-spinner"
                  aria-hidden="true"
                />
              </div>
            )}
            {(phase === "ready" ||
              phase === "round_ready" ||
              phase === "won" ||
              phase === "lost") && (
              <div
                className={`buzz-center-message phase-${phase} ${messageTone}${phase === "won" || phase === "lost" ? ` auth-outcome-panel ${phase === "won" ? "is-success" : "is-retry"}` : ""}`}
                role="status"
              >
                {phase === "ready" && (
                  <>
                    <h4>Ready to play?</h4>
                    <Button
                      variant="contained"
                      size="large"
                      className="auth-outcome-button is-success"
                      aria-label="Start game"
                      onClick={() => beginRef.current()}
                    >
                      <Play size={24} fill="currentColor" aria-hidden="true" />
                    </Button>
                  </>
                )}
                {phase === "round_ready" && (
                  <>
                    <h4>Paused</h4>
                    <Button
                      variant="contained"
                      size="large"
                      aria-label="Resume game"
                      title="Resume game"
                      onClick={() => beginRef.current()}
                    >
                      <Play size={24} fill="currentColor" aria-hidden="true" />
                    </Button>
                  </>
                )}
                {phase === "won" && (
                  <>
                    <h4>You won!</h4>
                    <p>You beat the bot.</p>
                    <AuthActionButton
                      action="continue"
                      onClick={() => {
                        if (winToken) callbacksRef.current.onWin(winToken);
                      }}
                    >
                      Continue
                    </AuthActionButton>
                  </>
                )}
                {phase === "lost" && (
                  <>
                    {lateBuzzIcon}
                    <h4>Game over</h4>
                    <p>{lateBuzzDetail ?? feedbackMessage}</p>
                    <AuthActionButton
                      action="retry"
                      onClick={() => setAttempt((value) => value + 1)}
                    >
                      Try Again
                    </AuthActionButton>
                  </>
                )}
              </div>
            )}
          </div>
        </>
      )}
      {phase === "error" && (
        <div
          className="buzz-finale bad is-error-overlay auth-outcome-panel is-retry"
          role="alert"
        >
          <h4>Try again</h4>
          <p>{cooldown.message}</p>
          <AuthActionButton
            action="retry"
            disabled={cooldown.remainingSeconds > 0}
            onClick={() => {
              if (error?.recovery === "restart") {
                callbacksRef.current.onWordGuessExpired();
              } else if (tokenExpired(wordGuessToken)) {
                setError(
                  getAuthErrorFeedback(
                    new AuthChallengeError("WORD_GUESS_TOKEN_EXPIRED"),
                    "fruit_buzz",
                  ),
                );
              } else {
                setAttempt((value) => value + 1);
              }
            }}
          >
            <span>
              {error?.recovery === "restart"
                ? "Restart Verification"
                : "Try Again"}
            </span>
          </AuthActionButton>
        </div>
      )}
    </div>
  );
}
