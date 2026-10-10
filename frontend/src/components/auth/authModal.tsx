import { useState, useEffect, useCallback } from "react";
import { RotateCcw } from "lucide-react";
import axios from "axios";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  CircularProgress,
} from "@mui/material";
import { WordGuessGame } from "./wordGuess";
import { FruitBuzz } from "./fruitBuzz";
import { AuthActionButton } from "./authActionButton";
import { useAuth } from "../../hooks/useAuthHook";
import { useAuthCooldown } from "../../hooks/useAuthCooldown";
import {
  getAuthErrorFeedback,
  type AuthErrorFeedback,
} from "../../utils/authErrors";
import {
  getCaptchaId,
  getCaptchaImage,
  verifyCaptcha,
  getWordGuessId,
  submitWordGuess,
  verifySecurityQuestions,
  getRemovePlayerToken,
} from "../../services/api/auth";
import "./authModal.css";

interface AuthModalProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly onSuccess: () => void;
}

// Count challenges, not requests. Finishing verification is still part of the last step.
const AUTH_STEPS = ["captcha", "word_guess", "fruit_buzz", "security"] as const;
type AuthStep = (typeof AUTH_STEPS)[number];
// TODO let the backend communicate that upon Word Guess session start and the frontend
// dynamically reacts to it
const MAX_WORD_GUESS_ATTEMPTS = 6; // Guesses allowed per Word Guess

export function AuthModal({ open, onClose, onSuccess }: AuthModalProps) {
  const [currentStep, setCurrentStep] = useState<AuthStep>("captcha");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<AuthErrorFeedback | null>(null);
  const cooldown = useAuthCooldown(error);
  const retryBlocked = cooldown.remainingSeconds > 0;
  const { login } = useAuth();

  // Captcha state
  const [captchaId, setCaptchaId] = useState("");
  const [captchaImageUrl, setCaptchaImageUrl] = useState("");
  const [captchaImageLoaded, setCaptchaImageLoaded] = useState(false);
  const [captchaAnswer, setCaptchaAnswer] = useState("");
  const [captchaIncorrect, setCaptchaIncorrect] = useState(false);
  // The server dropped the challenge (too many wrong answers or expired), so
  // only loading a new one can help.
  const [captchaGone, setCaptchaGone] = useState(false);
  const [captchaToken, setCaptchaToken] = useState("");

  // Word Guess state
  const [wordGuessId, setWordGuessId] = useState("");
  const [wordGuessToken, setWordGuessToken] = useState("");
  const [fruitBuzzToken, setFruitBuzzToken] = useState("");
  const [securityToken, setSecurityToken] = useState("");

  // Security questions state
  const [securityAnswers, setSecurityAnswers] = useState({
    most_annoying_card: "",
    most_skillful_card: "",
    most_mousey_card: "",
  });

  // Initialize captcha when modal opens
  useEffect(() => {
    if (open && currentStep === "captcha") {
      initializeCaptcha();
    }
  }, [open, currentStep]);

  const initializeCaptcha = async () => {
    setLoading(true);
    setError(null);
    setCaptchaIncorrect(false);
    setCaptchaGone(false);
    setCaptchaId("");
    setCaptchaImageUrl("");
    setCaptchaImageLoaded(false);
    setCaptchaAnswer("");
    setCaptchaToken("");
    try {
      const { captcha_id } = await getCaptchaId();
      setCaptchaId(captcha_id);

      const imageBlob = await getCaptchaImage(captcha_id);
      const imageUrl = URL.createObjectURL(imageBlob);
      setCaptchaImageUrl(imageUrl);
    } catch (err) {
      setError(getAuthErrorFeedback(err, "captcha_load"));
      console.error("Captcha initialization error:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleCaptchaSubmit = async () => {
    if (loading || retryBlocked || captchaGone || !captchaAnswer.trim()) return;

    setLoading(true);
    setError(null);
    setCaptchaIncorrect(false);
    try {
      const { captcha_token } = await verifyCaptcha({
        captcha_id: captchaId,
        answer: captchaAnswer,
      });
      setCaptchaToken(captcha_token);
      await initializeWordGuess(captcha_token);
    } catch (err) {
      setError(getAuthErrorFeedback(err, "captcha"));
      // Only a wrong answer should shake the input, not an expired challenge or connection error.
      if (axios.isAxiosError(err)) {
        const code = err.response?.data?.detail?.code;
        setCaptchaIncorrect(
          code === "CAPTCHA_INCORRECT" ||
            code === "CAPTCHA_ATTEMPTS_EXHAUSTED" ||
            (!code &&
              (err.response?.status === 401 || err.response?.status === 403)),
        );
        setCaptchaGone(
          code === "CAPTCHA_ATTEMPTS_EXHAUSTED" || code === "CAPTCHA_EXPIRED",
        );
      }
      console.error("Captcha verification error:", err);
    } finally {
      setLoading(false);
    }
  };

  const initializeWordGuess = async (token = captchaToken) => {
    if (retryBlocked) return;
    setLoading(true);
    setError(null);
    // Mount the whole next stage immediately so waiting for its ID doesn't collapse the modal.
    setWordGuessId("");
    setCurrentStep("word_guess");
    try {
      const { word_guess_id } = await getWordGuessId(token);
      setWordGuessId(word_guess_id);
    } catch (err) {
      setError(getAuthErrorFeedback(err, "word_guess_load"));
      console.error("Word Guess initialization error:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleWordGuess = async (guess: string) => {
    setError(null);
    try {
      const result = await submitWordGuess(captchaToken, {
        word_guess_id: wordGuessId,
        guess,
      });

      // A correct guess returns a word_guess_token
      if (result.is_solution && result.word_guess_token) {
        setWordGuessToken(result.word_guess_token);
        // Don't transition immediately - let the Word Guess component show success popup
        // The next step starts after the Word Guess success popup is closed.
        return {
          correct: true,
          feedback: {
            evaluation: result.evaluation as Record<number, string>,
            solution: result.solution ?? "",
          },
        };
      }

      // Return feedback for incorrect guesses
      return {
        correct: false,
        feedback: {
          evaluation: result.evaluation as Record<number, string>,
          remaining_guesses: result.remaining_guesses,
          solution: result.solution ?? "",
        },
      };
    } catch (err) {
      console.error("Word Guess submit error:", err);
      const feedback = getAuthErrorFeedback(err, "word_guess_submit");
      if (feedback.recovery) setError(feedback);
      throw err;
    }
  };

  const handleWordGuessFailure = async () => {
    await initializeWordGuess();
  };

  const handleWordGuessSuccess = () => {
    // Fruit Buzz must be won before security questions accept a token.
    setCurrentStep("fruit_buzz");
  };

  const handleFruitBuzzWin = useCallback((token: string) => {
    setFruitBuzzToken(token);
    setCurrentStep("security");
  }, []);

  const handleWordGuessExpired = useCallback(() => {
    // Restart only after the player has seen the expiry message and chosen to continue.
    setCurrentStep("captcha");
    setCaptchaId("");
    setCaptchaImageUrl("");
    setCaptchaImageLoaded(false);
    setCaptchaAnswer("");
    setCaptchaToken("");
    setWordGuessId("");
    setWordGuessToken("");
    setFruitBuzzToken("");
    setSecurityToken("");
    setSecurityAnswers({
      most_annoying_card: "",
      most_skillful_card: "",
      most_mousey_card: "",
    });
    setError(null);
  }, []);

  const handleSecuritySubmit = async () => {
    if (loading || retryBlocked) return;
    const { most_annoying_card, most_skillful_card, most_mousey_card } =
      securityAnswers;
    if (!fruitBuzzToken) {
      setError({
        message: "Complete Fruit Buzz before continuing.",
        recovery: "restart",
      });
      return;
    }
    if (
      !most_annoying_card.trim() ||
      !most_skillful_card.trim() ||
      !most_mousey_card.trim()
    ) {
      setError({ message: "Please answer all three questions." });
      return;
    }

    setLoading(true);
    setError(null);
    let verifiedSecurityToken = securityToken;
    try {
      // Keep accepted answers verified if only the final token request fails.
      // Retrying that request shouldn't spend another answer attempt.
      if (!verifiedSecurityToken) {
        const { security_token } = await verifySecurityQuestions(
          fruitBuzzToken,
          { most_annoying_card, most_skillful_card, most_mousey_card },
        );
        verifiedSecurityToken = security_token;
        setSecurityToken(security_token);
      }

      const { remove_player_token } = await getRemovePlayerToken(
        verifiedSecurityToken,
      );
      login(remove_player_token);
      resetAuthFlow();
      onSuccess();
      onClose();
    } catch (err) {
      setError(
        getAuthErrorFeedback(
          err,
          verifiedSecurityToken ? "finish" : "security",
        ),
      );
      console.error("Security questions error:", err);
    } finally {
      setLoading(false);
    }
  };

  // Set all intermediate step values to empty and restart with captcha
  const resetAuthFlow = () => {
    setCurrentStep("captcha");
    setCaptchaId("");
    setCaptchaImageUrl("");
    setCaptchaImageLoaded(false);
    setCaptchaAnswer("");
    setCaptchaToken("");
    setWordGuessId("");
    setWordGuessToken("");
    setFruitBuzzToken("");
    setSecurityToken("");
    setSecurityAnswers({
      most_annoying_card: "",
      most_skillful_card: "",
      most_mousey_card: "",
    });
    setError(null);
  };

  const handleClose = () => {
    resetAuthFlow();
    onClose();
  };

  const handleRestart = () => {
    resetAuthFlow();
    // Changing steps triggers initialization; an already-active CAPTCHA needs it explicitly.
    if (currentStep === "captcha") void initializeCaptcha();
  };

  const renderCaptchaStep = () => (
    <div className="auth-step captcha-step">
      <h3 className="auth-stage-heading">Prove that you are not a robot</h3>
      <div className="captcha-container">
        <div
          className="captcha-image-slot"
          aria-busy={
            !captchaImageLoaded && (loading || Boolean(captchaImageUrl))
          }
        >
          {!captchaImageLoaded && (
            <div className="captcha-image-placeholder" role="status">
              {loading || captchaImageUrl ? (
                <CircularProgress
                  className="auth-loading-spinner"
                  size={24}
                  aria-label="Loading CAPTCHA image"
                />
              ) : (
                <span>CAPTCHA image</span>
              )}
            </div>
          )}
          {captchaImageUrl && (
            <img
              key={captchaImageUrl}
              src={captchaImageUrl}
              alt="CAPTCHA"
              className={`captcha-image${captchaImageLoaded ? "" : " is-loading"}`}
              onLoad={() => setCaptchaImageLoaded(true)}
              onError={() => {
                setCaptchaImageUrl("");
                setCaptchaImageLoaded(false);
                setError({
                  message:
                    "Couldn't load the CAPTCHA image. Restart the CAPTCHA.",
                });
              }}
            />
          )}
        </div>
        <input
          type="text"
          aria-label="CAPTCHA answer"
          aria-invalid={captchaIncorrect}
          className={captchaIncorrect ? "is-incorrect" : undefined}
          value={captchaAnswer}
          onChange={(e) => setCaptchaAnswer(e.target.value)}
          placeholder="Enter the text you see"
          disabled={loading || captchaGone || !captchaImageLoaded}
          onKeyDown={(e) => e.key === "Enter" && handleCaptchaSubmit()}
        />
        <div className="captcha-buttons">
          <Button
            onClick={handleCaptchaSubmit}
            disabled={
              loading ||
              retryBlocked ||
              captchaGone ||
              !captchaImageLoaded ||
              !captchaAnswer.trim()
            }
            variant="contained"
            color="primary"
          >
            Verify
          </Button>
          <Button
            onClick={initializeCaptcha}
            disabled={loading || retryBlocked}
            variant="outlined"
            startIcon={<RotateCcw size={18} aria-hidden="true" />}
          >
            Refresh
          </Button>
        </div>
      </div>
    </div>
  );

  const renderWordGuessStep = () => (
    <div className="auth-step">
      <WordGuessGame
        key={wordGuessId || "loading"}
        loading={loading}
        disabled={!wordGuessId || loading || Boolean(error?.recovery)}
        guessesAllowed={MAX_WORD_GUESS_ATTEMPTS}
        onGuess={handleWordGuess}
        onFailure={handleWordGuessFailure}
        onSuccess={handleWordGuessSuccess}
      />
    </div>
  );

  const renderFruitBuzzStep = () =>
    wordGuessToken ? (
      <FruitBuzz
        wordGuessToken={wordGuessToken}
        onWin={handleFruitBuzzWin}
        onWordGuessExpired={handleWordGuessExpired}
      />
    ) : null;

  const renderSecurityStep = () => (
    <div className="auth-step">
      <h3 className="auth-stage-heading">
        Prove your elite Clash Royale knowledge
      </h3>
      <div className="security-questions">
        <div className="question-group">
          <label htmlFor="annoying-card">
            What is the most annoying card in Clash Royale?
          </label>
          <input
            id="annoying-card"
            type="text"
            value={securityAnswers.most_annoying_card}
            onChange={(e) =>
              setSecurityAnswers((prev) => ({
                ...prev,
                most_annoying_card: e.target.value,
              }))
            }
            placeholder="e.g., Mega Knight"
            disabled={loading || Boolean(securityToken)}
            autoComplete="off"
            spellCheck="false"
            data-form-type="other"
          />
        </div>
        <div className="question-group">
          <label htmlFor="skillful-card">
            What is the most skillful card in Clash Royale?
          </label>
          <input
            id="skillful-card"
            type="text"
            value={securityAnswers.most_skillful_card}
            onChange={(e) =>
              setSecurityAnswers((prev) => ({
                ...prev,
                most_skillful_card: e.target.value,
              }))
            }
            placeholder="e.g., X-Bow"
            disabled={loading || Boolean(securityToken)}
            autoComplete="off"
            spellCheck="false"
            data-form-type="other"
          />
        </div>
        <div className="question-group">
          <label htmlFor="mousey-card">
            What is the most 'mousey/cutie/sweet' card in Clash Royale?
          </label>
          <input
            id="mousey-card"
            type="text"
            value={securityAnswers.most_mousey_card}
            onChange={(e) =>
              setSecurityAnswers((prev) => ({
                ...prev,
                most_mousey_card: e.target.value,
              }))
            }
            placeholder="e.g., Heal Spirit"
            disabled={loading || Boolean(securityToken)}
            autoComplete="off"
            spellCheck="false"
            data-form-type="other"
          />
        </div>
        <AuthActionButton
          action="continue"
          showIcon={false}
          onClick={handleSecuritySubmit}
          busy={loading}
          disabled={retryBlocked}
          className="security-submit"
        >
          {securityToken ? "Finish Verification" : "Complete Authentication"}
        </AuthActionButton>
      </div>
    </div>
  );

  const getStepContent = () => {
    switch (currentStep) {
      case "captcha":
        return renderCaptchaStep();
      case "word_guess":
        return renderWordGuessStep();
      case "fruit_buzz":
        return renderFruitBuzzStep();
      case "security":
        return renderSecurityStep();
      default:
        return null;
    }
  };

  // A restart menu belongs over the board; putting it in the footer would take
  // height away from the challenge and introduce scroll on smaller screens.
  const needsRecovery = Boolean(
    error && (error.recovery || (currentStep === "word_guess" && !wordGuessId)),
  );
  const errorFeedback = error && (
    <div
      className={`auth-error-overlay${needsRecovery ? " auth-outcome-panel is-retry" : ""}`}
      role="alert"
    >
      {needsRecovery && <h4>Try again</h4>}
      <span>{cooldown.message}</span>
      {error.recovery && (
        <AuthActionButton
          action="retry"
          onClick={
            error.recovery === "word_guess" ? handleWordGuessFailure : handleRestart
          }
          disabled={loading || retryBlocked}
        >
          {error.recovery === "word_guess"
            ? "Restart Word Guess"
            : "Restart Verification"}
        </AuthActionButton>
      )}
      {currentStep === "word_guess" && !wordGuessId && !error.recovery && (
        <AuthActionButton
          action="retry"
          onClick={() => initializeWordGuess()}
          busy={loading}
          disabled={retryBlocked}
        >
          Retry Word Guess
        </AuthActionButton>
      )}
    </div>
  );

  return (
    <Dialog
      open={open}
      scroll="paper"
      disableEscapeKeyDown
      maxWidth={currentStep === "fruit_buzz" ? "lg" : "md"}
      fullWidth
      className={`auth-modal ${currentStep === "fruit_buzz" ? "fruit-buzz-modal" : currentStep === "word_guess" ? "word-guess-modal" : ""}`}
    >
      <DialogTitle>
        <span>Authentication</span>
        <span
          className="auth-step-count"
          aria-label={`Step ${AUTH_STEPS.indexOf(currentStep) + 1} of ${AUTH_STEPS.length}`}
          aria-live="polite"
        >
          {AUTH_STEPS.indexOf(currentStep) + 1}/{AUTH_STEPS.length}
        </span>
      </DialogTitle>
      <DialogContent>
        {loading &&
          currentStep !== "word_guess" &&
          !(currentStep === "captcha" && !captchaImageLoaded) && (
            <div className="loading-overlay">
              <CircularProgress
                className="auth-loading-spinner"
                size={36}
                aria-label="Loading"
              />
            </div>
          )}
        {getStepContent()}
        {needsRecovery && (
          <div className="auth-recovery-overlay">{errorFeedback}</div>
        )}
      </DialogContent>
      <DialogActions>
        <div className="auth-feedback-anchor">
          {!needsRecovery && errorFeedback}
        </div>
        <Button onClick={handleClose} color="error" variant="outlined">
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  );
}
