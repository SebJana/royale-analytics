import { useState } from "react";
import { AlertCircle, RotateCcw } from "lucide-react";
import "./playerError.css";

const MAX_RETRY_ATTEMPTS = 3;

export type PlayerErrorSource = {
  label: string;
  failed: boolean;
  retry: () => Promise<unknown>;
};

export function PlayerError({
  sources,
  message,
  title = "Couldn't load this page",
  compact = false,
}: Readonly<{
  sources: PlayerErrorSource[];
  message?: string;
  title?: string;
  compact?: boolean;
}>) {
  const [isRetrying, setIsRetrying] = useState(false);
  const [retryAttempts, setRetryAttempts] = useState(0);
  const retriesExhausted = retryAttempts >= MAX_RETRY_ATTEMPTS && !isRetrying;
  const failedSources = sources.filter((source) => source.failed);
  const names = failedSources.map((source) => source.label);
  const sourceList =
    names.length > 1
      ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
      : names[0];

  const retry = async () => {
    if (isRetrying || retryAttempts >= MAX_RETRY_ATTEMPTS) return;
    setRetryAttempts((attempts) => attempts + 1);
    setIsRetrying(true);
    // Retry every failed request; one successful request should not hide another failure.
    try {
      await Promise.allSettled(failedSources.map((source) => source.retry()));
    } finally {
      setIsRetrying(false);
    }
  };

  return (
    <section
      className={`player-page-error${compact ? " is-compact" : ""}`}
      role="alert"
    >
      <AlertCircle className="player-page-error-icon" aria-hidden="true" />
      <h2>{title}</h2>
      <p>
        {retriesExhausted
          ? `Still couldn't load the ${sourceList}. Please try again later.`
          : retryAttempts > 0 && !isRetrying
            ? `Trying again didn't work. The ${sourceList} still couldn't load.`
            : (message ??
              `Looks like the ${sourceList} went missing on the way here. Try again in a moment.`)}
      </p>
      {!retriesExhausted && (
        <div className="player-page-error-actions">
          <button
            type="button"
            onClick={() => void retry()}
            disabled={isRetrying}
          >
            <RotateCcw size={18} aria-hidden="true" />
            {isRetrying ? "Trying again..." : "Try again"}
          </button>
        </div>
      )}
    </section>
  );
}
