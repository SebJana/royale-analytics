import { useEffect, useId, useLayoutEffect, useState } from "react";
import {
  AlertCircle,
  CircleCheck,
  Clock,
  LockKeyhole,
  LockKeyholeOpen,
} from "lucide-react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { trackPlayer, untrackPlayer } from "../services/api/trackedPlayers";
import { pluralize } from "../utils/plural";
import { formatNumberWithSuffix } from "../utils/number";
import { validatePlayerTagSyntax } from "../utils/playerTag";
import {
  TRACKED_PLAYER_COUNT_QUERY_KEY,
  useTrackedPlayerCount,
} from "../hooks/useTrackedPlayerCount";
import { useTotalBattleCount } from "../hooks/useTotalBattleCount";
import { useAuth } from "../hooks/useAuthHook";
import { PlayerSearch } from "../components/playerSearch/playerSearch";
import { PLAYER_SEARCH_QUERY_KEY } from "../hooks/usePlayerSearch";
import { AuthModal } from "../components/auth/authModal";
import Lottie from "lottie-react";
import construction from "../assets/animations/construction.json";
import CircularProgress from "@mui/material/CircularProgress";
import axios from "axios";
import { StatCard } from "../components/statCard/statCard";
import "./home.css";

function getErrorMessage(error: unknown): string {
  if (
    !axios.isAxiosError<{
      detail?: string | { code?: string; message?: string };
    }>(error)
  ) {
    return error instanceof Error
      ? error.message
      : "Something went wrong. Please try again.";
  }

  if (!error.response) return "Could not reach the server. Please try again.";

  const { status, data } = error.response;
  const detail = data?.detail;
  const code = typeof detail === "object" ? detail?.code : undefined;

  // Check the backend code first; a 502 can also come from Clash Royale.
  if (code === "INVALID_PLAYER_TAG")
    return "Invalid player tag. Enter a tag like #YYRJQY28.";
  if (code === "PLAYER_NOT_FOUND")
    return "Player not found. Check the tag and try again.";
  if (code === "PLAYER_NOT_TRACKED") return "That player isn't being tracked.";
  if (code === "TRACKING_CAPACITY_REACHED")
    return "The maximum number of tracked players is reached. No new players can be added right now.";
  if (code === "CR_API_AUTH_FAILED")
    return (
      "Clash Royale rejected the API connection, so this player can't be tracked right now. " +
      "Please try again later."
    );
  if (code === "CR_API_MAINTENANCE")
    return (
      "Clash Royale is currently undergoing maintenance, so this player can't be tracked right now. " +
      "Please try again later."
    );
  if (code === "CR_API_UNAVAILABLE" || code === "CR_API_INVALID_RESPONSE")
    return "Could not check the player with Clash Royale. Please try again later.";
  if (status === 401 || status === 403)
    return "Authorization failed. Please verify again.";
  if (status === 429) return "Too many requests. Please try again shortly.";
  if (status >= 500)
    return "The server could not complete the request. Please try again later.";

  return (
    (typeof detail === "string" ? detail : detail?.message) ??
    "Something went wrong. Please try again."
  );
}

type HomeStatus = {
  tone: "success" | "error" | "wait";
  title: string;
  message: string;
};

const STATUS_ICONS = {
  success: CircleCheck,
  error: AlertCircle,
  wait: Clock,
} as const;

// Module level, so the slot sees the same object while it stays open.
const AUTH_STATUS: HomeStatus = {
  tone: "success",
  title: "Verification complete",
  message: "Enter a player tag to remove.",
};

/**
 * Turns a failed add or remove into a status. Rate limits and outages pass on
 * their own, so they read as "wait" rather than as a mistake in the tag.
 */
function getErrorStatus(error: unknown, title: string): HomeStatus {
  const response = axios.isAxiosError<{ detail?: { code?: string } | string }>(
    error,
  )
    ? error.response
    : undefined;
  const detail = response?.data?.detail;
  const code = typeof detail === "object" ? detail?.code : undefined;
  const isTemporary =
    response?.status === 429 ||
    code === "CR_API_MAINTENANCE" ||
    code === "CR_API_UNAVAILABLE";
  return {
    tone: isTemporary ? "wait" : "error",
    title,
    message: getErrorMessage(error),
  };
}

/**
 * Outcome callout under a home form. Shared by add, remove and verification,
 * so every status in the panels reads the same way.
 *
 * The slot stays mounted and opens or collapses its height, so the panels
 * below slide instead of jumping when a status comes or goes.
 */
function HomeStatusMessage({
  status,
  id,
}: Readonly<{ status: HomeStatus | null; id?: string }>) {
  // Keeps the last status rendered while the slot collapses; dropping it
  // right away would empty the box before it finishes closing.
  const [shown, setShown] = useState(status);
  if (status && status !== shown) setShown(status);
  const open = status !== null;
  const Icon = shown ? STATUS_ICONS[shown.tone] : null;

  return (
    <div
      className={`home-status-slot${open ? " is-open" : ""}`}
      aria-hidden={!open}
      inert={!open}
    >
      <div className="home-status-clip">
        {shown && Icon && (
          <div
            id={id}
            className={`home-status-message is-${shown.tone}`}
            role={shown.tone === "success" ? "status" : "alert"}
          >
            <Icon className="home-status-icon" size={20} aria-hidden="true" />
            <div className="home-status-text">
              <strong>{shown.title}</strong>
              <span>{shown.message}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function HomePage() {
  const queryClient = useQueryClient();

  // The window keeps its scroll position across routes, so coming from far
  // down a player page would open home at its bottom, footer first. Before
  // paint, so that position never shows.
  useLayoutEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  // The API indexes adds and removes instantly; the cached count and search
  // results would still show the old state.
  const refreshTrackedPlayers = () => {
    void queryClient.invalidateQueries({
      queryKey: [TRACKED_PLAYER_COUNT_QUERY_KEY],
    });
    void queryClient.invalidateQueries({ queryKey: [PLAYER_SEARCH_QUERY_KEY] });
  };

  const {
    data: playerCount,
    isPending: playerCountPending,
    isError: playerCountError,
  } = useTrackedPlayerCount();

  const {
    data: battleCount,
    isPending: battleCountPending,
    isError: battleCountError,
  } = useTotalBattleCount();

  const [selectedPlayerTag, setSelectedPlayerTag] = useState("");
  const [addedPlayerTag, setAddedPlayerTag] = useState("");
  const [untrackedPlayerTag, setUntrackedPlayerTag] = useState("");
  const [trackingPlayer, setTrackingPlayer] = useState(false);
  const [untrackingPlayer, setUntrackingPlayer] = useState(false);
  const [trackingStatus, setTrackingStatus] = useState<HomeStatus | null>(null);
  const [untrackingStatus, setUntrackingStatus] = useState<HomeStatus | null>(
    null,
  );
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showAuthSuccess, setShowAuthSuccess] = useState(false);
  const trackingStatusId = useId();
  const untrackingStatusId = useId();
  const navigate = useNavigate();
  const { isAuthenticated, checkAuthStatus } = useAuth();

  useEffect(() => {
    if (!showAuthSuccess) return;
    const timer = window.setTimeout(() => setShowAuthSuccess(false), 5000);
    return () => window.clearTimeout(timer);
  }, [showAuthSuccess]);

  // A visit without cached counts loads them behind this spinner; the count
  // hooks keep them for a short while. A refetch keeps the page and its
  // status messages.
  if (playerCountPending || battleCountPending)
    return <CircularProgress className="home-loading-spinner" />;
  // A failed refetch keeps the previous counts on screen
  if ((playerCountError && !playerCount) || (battleCountError && !battleCount))
    return (
      <>
        <Lottie
          animationData={construction}
          loop={true}
          className="lottie-animation"
        />
        <h2 className="home-fatal-error-message">
          We're having trouble right now. Please try again shortly.
        </h2>
      </>
    );

  function canEnableViewButton() {
    /**
     * Helper function to check if the view button should be able to be clickable
     */
    if (!selectedPlayerTag) {
      return false;
    }
    if (!validatePlayerTagSyntax(selectedPlayerTag)) {
      return false;
    }
    return true;
  }

  const handleViewClick = () => {
    if (selectedPlayerTag) {
      // Navigate user to the selected player profile page
      navigate(`/player/${encodeURIComponent(selectedPlayerTag)}/battles`);
    }
  };

  // Any field or button in use makes every shown outcome stale, so starting
  // something new in one panel also clears the others.
  const clearStatuses = () => {
    setTrackingStatus(null);
    setUntrackingStatus(null);
    setShowAuthSuccess(false);
  };

  const handleAddPlayerClick = async () => {
    if (!addedPlayerTag) return;

    setTrackingPlayer(true);
    clearStatuses();

    try {
      const result = await trackPlayer(addedPlayerTag);
      setTrackingStatus({
        tone: "success",
        title: result.status,
        message: result.tag,
      });
      refreshTrackedPlayers();

      // Clear the input field
      setAddedPlayerTag("");
    } catch (error) {
      setTrackingStatus(getErrorStatus(error, "Couldn't add player"));
    } finally {
      setTrackingPlayer(false);
    }
  };

  const handleUntrackPlayerClick = async () => {
    if (!untrackedPlayerTag) return;
    clearStatuses();
    if (!validatePlayerTagSyntax(untrackedPlayerTag)) {
      setUntrackingStatus({
        tone: "error",
        title: "Couldn't remove player",
        message: "Invalid player tag. Enter a tag like #YYRJQY28.",
      });
      return;
    }

    // Check if user is authenticated
    if (!checkAuthStatus()) {
      setShowAuthModal(true);
      return;
    }

    setUntrackingPlayer(true);

    try {
      const result = await untrackPlayer(untrackedPlayerTag);
      setUntrackingStatus({
        tone: "success",
        title: result.status,
        message: result.tag,
      });
      refreshTrackedPlayers();

      // Clear the input field
      setUntrackedPlayerTag("");
    } catch (error) {
      setUntrackingStatus(getErrorStatus(error, "Couldn't remove player"));
    } finally {
      setUntrackingPlayer(false);
    }
  };

  const handleAuthSuccess = () => {
    setShowAuthModal(false);
    clearStatuses();
    setShowAuthSuccess(true);
  };

  return (
    <div className="home-page">
      <div className="home-container">
        <div className="home-header">
          <img
            src="/crown.png"
            alt="Clash Royale Crown"
            className="home-icon"
          />
          <h1 className="home-title">Clash Royale Analytics</h1>
          <div className="home-stat-cards-container">
            <StatCard
              value={formatNumberWithSuffix(
                playerCount?.activePlayerCount ?? 0,
              )}
              label={`Tracked ${pluralize(
                playerCount?.activePlayerCount ?? 0,
                "Player",
                "Players",
              )}`}
            />
            <StatCard
              value={formatNumberWithSuffix(battleCount?.totalBattleCount ?? 0)}
              label={`${pluralize(
                battleCount?.totalBattleCount ?? 0,
                "Battle",
                "Battles",
              )} on record`}
            />
          </div>
        </div>
        <div className="player-selection">
          <div className="search-section">
            <h2 className="section-header">View Players</h2>
            <p className="section-description">
              Search and view analytics for players already being tracked in our
              system.
            </p>
            {/* TODO Add an "Explore most popular players" button that pages
            through all tracked players, most profile views in the last 30 days
            first. Search only shows the best matches for a query, so this is
            the way to discover players without knowing a name. Needs the
            backend list from the TODO above GET /players/count in
            players_tracked.py. */}
            <PlayerSearch
              onSelectPlayer={(player) => {
                setSelectedPlayerTag(player?.tag ?? "");
                clearStatuses();
              }}
              onInput={clearStatuses}
            />
            <button
              className="view-button"
              onClick={handleViewClick}
              disabled={!canEnableViewButton()}
            >
              View Player
            </button>
          </div>
          {/* TODO add jump to player button popup after adding one so one
          can instantly view the newly tracked player (has to be some delay tho
          to give the queue a chance to ingest that player) */}
          <div className="adding-section">
            <h2 className="section-header">Add New Player</h2>
            <p className="section-description">
              Enter a player tag to start tracking their battles, decks, and
              performance analytics.
            </p>
            <input
              type="text"
              placeholder="Enter player tag... (e.g. #YYRJQY28)"
              value={addedPlayerTag}
              aria-invalid={trackingStatus?.tone === "error"}
              aria-describedby={trackingStatus ? trackingStatusId : undefined}
              onChange={(e) => {
                setAddedPlayerTag(e.target.value);
                // The outcome belongs to the submitted tag, not the new one.
                clearStatuses();
              }}
            />
            <button
              className="add-button"
              onClick={handleAddPlayerClick}
              disabled={!addedPlayerTag || trackingPlayer}
            >
              {trackingPlayer ? "Adding Player..." : "Add Player"}
            </button>

            <HomeStatusMessage status={trackingStatus} id={trackingStatusId} />
          </div>
          <div
            className={`untrack-section${showAuthSuccess && isAuthenticated ? " untrack-section-unlocked" : ""}`}
          >
            <h2 className="section-header">
              Remove Tracked Player
              <span
                className={`untrack-auth-status${isAuthenticated ? " is-unlocked" : ""}`}
                role="img"
                aria-label={
                  isAuthenticated ? "Verified" : "Verification required"
                }
                title={isAuthenticated ? "Verified" : "Verification required"}
              >
                <span className="untrack-auth-icon" aria-hidden="true">
                  {isAuthenticated ? (
                    <LockKeyholeOpen size={24} />
                  ) : (
                    <LockKeyhole size={24} />
                  )}
                </span>
              </span>
            </h2>
            <p className="section-description">
              {isAuthenticated
                ? "Enter a player tag to stop tracking their activity."
                : "Verify to stop tracking a player's activity."}{" "}
              Previously stored data won't be deleted by this, you can always
              add the player back.
            </p>
            {isAuthenticated ? (
              <>
                <input
                  type="text"
                  aria-label="Player tag to remove"
                  placeholder="Enter player tag... (e.g. #YYRJQY28)"
                  value={untrackedPlayerTag}
                  aria-invalid={untrackingStatus?.tone === "error"}
                  aria-describedby={
                    untrackingStatus ? untrackingStatusId : undefined
                  }
                  onChange={(e) => {
                    setUntrackedPlayerTag(e.target.value);
                    // The outcome belongs to the submitted tag, and the
                    // verification hint has done its job once typing starts.
                    clearStatuses();
                  }}
                />
                <button
                  className="remove-button"
                  onClick={handleUntrackPlayerClick}
                  disabled={!untrackedPlayerTag || untrackingPlayer}
                >
                  {untrackingPlayer ? "Removing Player..." : "Remove Player"}
                </button>
              </>
            ) : (
              <button
                className="verify-remove-button"
                onClick={() => setShowAuthModal(true)}
              >
                Verify
              </button>
            )}

            <HomeStatusMessage
              status={showAuthSuccess && isAuthenticated ? AUTH_STATUS : null}
            />
            <HomeStatusMessage
              status={untrackingStatus}
              id={untrackingStatusId}
            />
          </div>
        </div>

        <AuthModal
          open={showAuthModal}
          onClose={() => setShowAuthModal(false)}
          onSuccess={handleAuthSuccess}
        />
      </div>
    </div>
  );
}

export default HomePage;
