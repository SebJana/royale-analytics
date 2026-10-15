import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { LockKeyhole, LockKeyholeOpen } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { trackPlayer, untrackPlayer } from "../services/api/trackedPlayers";
import { getTrackingErrorStatus } from "../utils/trackingErrors";
import { StatusMessage } from "../components/statusMessage/statusMessage";
import type { Status } from "../types/status";
import { pluralize } from "../utils/plural";
import { formatNumberWithSuffix } from "../utils/number";
import {
  ADD_PLAYER_PARAM,
  ADD_PLAYER_SECTION_ID,
  validatePlayerTagSyntax,
} from "../utils/playerTag";
import {
  TRACKED_PLAYER_COUNT_QUERY_KEY,
  useTrackedPlayerCount,
} from "../hooks/useTrackedPlayerCount";
import { useTotalBattleCount } from "../hooks/useTotalBattleCount";
import { useAuth } from "../hooks/useAuthHook";
import { PlayerSearch } from "../components/playerSearch/playerSearch";
import { PLAYER_SEARCH_QUERY_KEY } from "../hooks/usePlayerSearch";
import { TRACKED_STATE_QUERY_KEY } from "../hooks/useTrackedState";
import { AuthModal } from "../components/auth/authModal";
import Lottie from "lottie-react";
import construction from "../assets/animations/construction.json";
import CircularProgress from "@mui/material/CircularProgress";
import axios from "axios";
import { StatCard } from "../components/statCard/statCard";
import "./home.css";
import { AppTooltip } from "../components/tooltip/tooltip";

// Module level, so the slot sees the same object while it stays open.
const AUTH_STATUS: Status = {
  tone: "success",
  title: "Verification complete",
  message: "Enter a player tag to remove.",
};

// NOTE: Match the removal token codes of require_remove_player_token in the
// backend's core/deps.py.
const REMOVAL_TOKEN_CODES = new Set([
  "REMOVE_PLAYER_TOKEN_USED_UP",
  "REMOVE_PLAYER_TOKEN_EXPIRED",
]);

/** Returns the backend's error code of a failed request, if it sent one. */
function errorCode(error: unknown): string | undefined {
  if (!axios.isAxiosError<{ detail?: { code?: string } | string }>(error))
    return undefined;
  const detail = error.response?.data?.detail;
  return typeof detail === "object" ? detail?.code : undefined;
}

function HomePage() {
  const queryClient = useQueryClient();

  // The window keeps its scroll position across routes, so coming from far
  // down a player page would open home at its bottom, footer first. Before
  // paint, so that position never shows.
  useLayoutEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  // The API indexes adds and removes instantly; the cached count, search
  // results and battle opponents' tracked states would still show the old one.
  const refreshTrackedPlayers = () => {
    void queryClient.invalidateQueries({
      queryKey: [TRACKED_PLAYER_COUNT_QUERY_KEY],
    });
    void queryClient.invalidateQueries({ queryKey: [PLAYER_SEARCH_QUERY_KEY] });
    void queryClient.invalidateQueries({ queryKey: [TRACKED_STATE_QUERY_KEY] });
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
  const [trackingStatus, setTrackingStatus] = useState<Status | null>(null);
  const [untrackingStatus, setUntrackingStatus] = useState<Status | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showAuthSuccess, setShowAuthSuccess] = useState(false);
  const trackingStatusId = useId();
  const untrackingStatusId = useId();
  const navigate = useNavigate();
  const { isAuthenticated, checkAuthStatus, logout } = useAuth();

  // A battle's "add this player" link fills in the add form. The parameter
  // is dropped right away, so a reload or a later visit starts empty.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedAddTag = searchParams.get(ADD_PLAYER_PARAM);
  const addSectionRef = useRef<HTMLDivElement>(null);
  const addInputRef = useRef<HTMLInputElement>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [highlightAdd, setHighlightAdd] = useState(false);

  useEffect(() => {
    if (!requestedAddTag) return;
    setAddedPlayerTag(requestedAddTag);
    setShowAddForm(true);
    setSearchParams(
      (params) => {
        params.delete(ADD_PLAYER_PARAM);
        return params;
      },
      { replace: true },
    );
  }, [requestedAddTag, setSearchParams]);

  // The form only exists once the counts have loaded, so scrolling waits.
  // The layout effect above has scrolled to the top by then.
  const countsLoaded = !playerCountPending && !battleCountPending;
  useEffect(() => {
    if (!showAddForm || !countsLoaded) return;
    setShowAddForm(false);
    addSectionRef.current?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "center",
    });
    // Focused without a second scroll, so the smooth one is not cut short.
    addInputRef.current?.focus({ preventScroll: true });
    setHighlightAdd(true);
  }, [showAddForm, countsLoaded]);

  useEffect(() => {
    if (!highlightAdd) return;
    const timer = window.setTimeout(() => setHighlightAdd(false), 1600);
    return () => window.clearTimeout(timer);
  }, [highlightAdd]);

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
      // The API stores the profile and puts the player first in the battle
      // schedule, so the page is usually useful right away. Until the first
      // battle sync lands, the battles page polls for it. Under heavy load or
      // after a restart following long downtime, the schedule is crowded and
      // that first sync can take a while.
      setTrackingStatus({
        tone: "success",
        title: result.status,
        message: result.tag,
        link: {
          to: `/player/${encodeURIComponent(result.tag)}/battles`,
          label: "View Player",
        },
      });
      refreshTrackedPlayers();

      // Clear the input field
      setAddedPlayerTag("");
    } catch (error) {
      setTrackingStatus(getTrackingErrorStatus(error, "Couldn't add player"));
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
      setUntrackingStatus(
        getTrackingErrorStatus(error, "Couldn't remove player"),
      );
      // A spent or expired removal token is useless, so the next removal
      // opens verification instead of failing again.
      const code = errorCode(error);
      if (code && REMOVAL_TOKEN_CODES.has(code)) logout();
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
          <h1 className="home-title">Royale Analytics</h1>
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
          <div
            id={ADD_PLAYER_SECTION_ID}
            ref={addSectionRef}
            className={`adding-section${highlightAdd ? " adding-section-requested" : ""}`}
          >
            <h2 className="section-header">Add New Player</h2>
            <p className="section-description">
              Enter a player tag to start tracking their battles, decks, and
              performance analytics.
            </p>
            <input
              ref={addInputRef}
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

            <StatusMessage
              className="home-status-slot"
              status={trackingStatus}
              id={trackingStatusId}
            />
          </div>
          <div
            className={`untrack-section${showAuthSuccess && isAuthenticated ? " untrack-section-unlocked" : ""}`}
          >
            <h2 className="section-header">
              Remove Tracked Player
              <AppTooltip
                title={isAuthenticated ? "Verified" : "Verification required"}
                tapToShow
              >
                <span
                  className={`untrack-auth-status${isAuthenticated ? " is-unlocked" : ""}`}
                  role="img"
                  aria-label={
                    isAuthenticated ? "Verified" : "Verification required"
                  }
                >
                  <span className="untrack-auth-icon" aria-hidden="true">
                    {isAuthenticated ? (
                      <LockKeyholeOpen size={24} />
                    ) : (
                      <LockKeyhole size={24} />
                    )}
                  </span>
                </span>
              </AppTooltip>
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

            <StatusMessage
              className="home-status-slot"
              status={showAuthSuccess && isAuthenticated ? AUTH_STATUS : null}
            />
            <StatusMessage
              className="home-status-slot"
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
