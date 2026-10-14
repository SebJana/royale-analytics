import type { ComponentPropsWithoutRef } from "react";
import CircularProgress from "@mui/material/CircularProgress";
import { ArrowRight, RotateCcw } from "lucide-react";
import "./authOutcome.css";

type AuthActionButtonProps = Omit<
  ComponentPropsWithoutRef<"button">,
  "type"
> & {
  readonly action: "continue" | "submit" | "retry";
  readonly busy?: boolean;
};

const DEFAULT_LABEL = {
  continue: "Continue",
  submit: "Submit",
  retry: "Try Again",
} as const;

/**
 * The main action of a stage or result panel, so always the app's primary
 * button: one per view, its role told by label and icon rather than color
 * (WCAG 1.4.1). Continue moves to the next stage with the View Player link's
 * arrow (home.tsx), submit sends the current step, retry starts a step over
 * with the restart icon that marks every retry, also the secondary Refresh
 * beside Verify.
 *
 * @param action - Which of the three, which also picks the default label
 * @param busy - Shows a spinner and blocks clicks while the action runs
 */
export function AuthActionButton({
  action,
  busy = false,
  className = "",
  disabled,
  children,
  ...props
}: AuthActionButtonProps) {
  return (
    <button
      {...props}
      type="button"
      className={`auth-button ${className}`}
      disabled={disabled || busy}
      aria-busy={busy}
    >
      {!busy && action === "retry" && (
        <RotateCcw size={18} aria-hidden="true" />
      )}
      {busy && (
        <CircularProgress size={16} color="inherit" aria-hidden="true" />
      )}
      {children ?? DEFAULT_LABEL[action]}
      {!busy && action === "continue" && (
        <ArrowRight
          className="auth-button-arrow"
          size={18}
          aria-hidden="true"
        />
      )}
    </button>
  );
}
