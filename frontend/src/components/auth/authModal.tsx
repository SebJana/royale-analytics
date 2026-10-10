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
import { WordleGame } from "./wordle";
import { HalliGalli } from "./halliGalli";
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
  getWordleId,
  submitWordleGuess,
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
const AUTH_STEPS = ["captcha", "wordle", "halli_galli", "security"] as const;
type AuthStep = (typeof AUTH_STEPS)[number];
// TODO let the backend communicate that upon wordle session start and the frontend
// dynamically reacts to it
const MAX_WORDLE_GUESSES_ALLOWED = 6; // Standard Wordle guess limit

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

  // Wordle state
  const [wordleId, setWordleId] = useState("");
  const [wordleToken, setWordleToken] = useState("");
  const [halliGalliToken, setHalliGalliToken] = useState("");
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
      await initializeWordle(captcha_token);
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

  const initializeWordle = async (token = captchaToken) => {
    if (retryBlocked) return;
    setLoading(true);
    setError(null);
    // Mount the whole next stage immediately so waiting for its ID doesn't collapse the modal.
    setWordleId("");
    setCurrentStep("wordle");
    try {
      const { wordle_id } = await getWordleId(token);
      setWordleId(wordle_id);
    } catch (err) {
      setError(getAuthErrorFeedback(err, "wordle_load"));
      console.error("Wordle initialization error:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleWordleGuess = async (guess: string) => {
    setError(null);
    try {
      const result = await submitWordleGuess(captchaToken, {
        wordle_id: wordleId,
        wordle_guess: guess,
      });

      // A correct guess returns a wordle_token
      if (result.is_solution && result.wordle_token) {
        setWordleToken(result.wordle_token);
        // Don't transition immediately - let the Wordle component show success popup
        // The next step starts after the Wordle success popup is closed.
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
      console.error("Wordle guess error:", err);
      const feedback = getAuthErrorFeedback(err, "wordle_guess");
      if (feedback.recovery) setError(feedback);
      throw err;
    }
  };

  const handleWordleFailure = async () => {
    await initializeWordle();
  };

  const handleWordleSuccess = () => {
    // Halli Galli must be won before security questions accept a token.
    setCurrentStep("halli_galli");
  };

  const handleHalliGalliWin = useCallback((token: string) => {
    setHalliGalliToken(token);
    setCurrentStep("security");
  }, []);

  const handleWordleExpired = useCallback(() => {
    // Restart only after the player has seen the expiry message and chosen to continue.
    setCurrentStep("captcha");
    setCaptchaId("");
    setCaptchaImageUrl("");
    setCaptchaImageLoaded(false);
    setCaptchaAnswer("");
    setCaptchaToken("");
    setWordleId("");
    setWordleToken("");
    setHalliGalliToken("");
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
    if (!halliGalliToken) {
      setError({
        message: "Complete Halli Galli before continuing.",
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
          halliGalliToken,
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
    setWordleId("");
    setWordleToken("");
    setHalliGalliToken("");
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

  const renderWordleStep = () => (
    <div className="auth-step">
      <WordleGame
        key={wordleId || "loading"}
        loading={loading}
        disabled={!wordleId || loading || Boolean(error?.recovery)}
        guessesAllowed={MAX_WORDLE_GUESSES_ALLOWED}
        onGuess={handleWordleGuess}
        onFailure={handleWordleFailure}
        onSuccess={handleWordleSuccess}
      />
    </div>
  );

  const renderHalliGalliStep = () =>
    wordleToken ? (
      <HalliGalli
        wordleToken={wordleToken}
        onWin={handleHalliGalliWin}
        onWordleExpired={handleWordleExpired}
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
      case "wordle":
        return renderWordleStep();
      case "halli_galli":
        return renderHalliGalliStep();
      case "security":
        return renderSecurityStep();
      default:
        return null;
    }
  };

  // A restart menu belongs over the board; putting it in the footer would take
  // height away from the challenge and introduce scroll on smaller screens.
  const needsRecovery = Boolean(
    error && (error.recovery || (currentStep === "wordle" && !wordleId)),
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
            error.recovery === "wordle" ? handleWordleFailure : handleRestart
          }
          disabled={loading || retryBlocked}
        >
          {error.recovery === "wordle"
            ? "Restart Wordle"
            : "Restart Verification"}
        </AuthActionButton>
      )}
      {currentStep === "wordle" && !wordleId && !error.recovery && (
        <AuthActionButton
          action="retry"
          onClick={() => initializeWordle()}
          busy={loading}
          disabled={retryBlocked}
        >
          Retry Wordle
        </AuthActionButton>
      )}
    </div>
  );

  return (
    <Dialog
      open={open}
      scroll="paper"
      disableEscapeKeyDown
      maxWidth={currentStep === "halli_galli" ? "lg" : "md"}
      fullWidth
      className={`auth-modal ${currentStep === "halli_galli" ? "halli-galli-modal" : currentStep === "wordle" ? "wordle-modal" : ""}`}
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
          currentStep !== "wordle" &&
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
