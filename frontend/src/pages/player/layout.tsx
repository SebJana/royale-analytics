import { Outlet, NavLink, useParams, useLocation } from "react-router-dom";
import {
  usePlayerProfile,
  getProfileErrorCode,
} from "../../hooks/usePlayerProfile";
import { House, Menu, X, ChevronLeft } from "lucide-react";
import { PlayerInfo } from "../../components/playerInfo/playerInfo";
import { PlayerInfoPlaceholder } from "../../components/playerInfo/playerInfoPlaceholder";
import { ParkedNotice } from "../../components/parkedNotice/parkedNotice";
import { TopPlayerNotice } from "../../components/topPlayerNotice/topPlayerNotice";
import { PlayerErrorBoundary } from "../../components/playerError/playerErrorBoundary";
import { useEffect, useLayoutEffect, useState } from "react";
import axios from "axios";
import {
  recordRecentPlayer,
  removeRecentPlayer,
} from "../../hooks/useRecentPlayers";
import { normalizePlayerTag } from "../../utils/playerTag";
import { useProfileView } from "../../hooks/useProfileView";
import { useSyncVersionRefresh } from "../../hooks/useSyncVersionRefresh";
import { clearBattlesView } from "../../utils/battlesView";
import Lottie from "lottie-react";
import emptyBox from "../../assets/animations/emptyBox.json";
import genericError from "../../assets/animations/404.json";

import CircularProgress from "@mui/material/CircularProgress";
import type { PlayerTracking } from "../../types/player";
import "./layout.css";

/**
 * Whether the top player on this page was just kept, so its notice stays to
 * show the outcome after the refetched profile reports it as regular.
 * Forgotten on another player: opened again, a kept player is regular and
 * needs no notice.
 *
 * @param playerTag - The player's tag as in the page URL
 * @returns Whether it was kept here, and the callback that marks it
 */
function useKeptPlayer(playerTag: string): readonly [boolean, () => void] {
  const [keptTag, setKeptTag] = useState<string | null>(null);
  // Adjusted while rendering, like derived state
  if (keptTag && keptTag !== playerTag) setKeptTag(null);
  return [keptTag === playerTag, () => setKeptTag(playerTag)] as const;
}

export default function PlayerLayout() {
  const { playerTag = "" } = useParams();
  const [menuOpen, setMenuOpen] = useState(false);
  // The player tracked again on this page, until it is parked anew.
  // confirmed: the profile has reported it as tracked since.
  const [resumed, setResumed] = useState<{
    tag: string;
    confirmed: boolean;
  } | null>(null);
  // Remounts the notice for a new pause, so a past success does not stay
  const [noticeEpisode, setNoticeEpisode] = useState(0);
  const [kept, markKept] = useKeptPlayer(playerTag);
  const encodedTag = encodeURIComponent(playerTag ?? "");
  const { pathname } = useLocation();

  // Close menu after navigation
  useEffect(() => setMenuOpen(false), [pathname]);

  // The window keeps its scroll position across routes, so a page opened
  // from far down the battles would start at its own bottom. The battles
  // page is left out: it restores its own position (battles.tsx), and this
  // effect runs after the page's, so it would undo that.
  useLayoutEffect(() => {
    if (/\/(decks|cards|plots)\/?$/.test(pathname)) window.scrollTo(0, 0);
  }, [pathname]);

  const {
    data: player,
    // Not isLoading: while the persisted cache restores, the query is paused,
    // which isLoading reports as not loading. The layout then rendered empty
    // for a frame, footer included, before the spinner.
    isPending: playerLoading,
    isError: isPlayerError,
    error: playerError,
  } = usePlayerProfile(playerTag ?? "");
  // The pages' cached battles and statistics follow the profile's version
  useSyncVersionRefresh(playerTag ?? "", player?.syncInfo?.syncVersion);

  // The profile comes from the database, so a failure means the player is
  // unknown, has no profile snapshot yet, or the backend has a problem.
  const errorCode = getProfileErrorCode(playerError);
  const isNotFound =
    playerError?.message === "Invalid player tag" ||
    errorCode === "INVALID_PLAYER_TAG" ||
    errorCode === "PLAYER_NOT_TRACKED";
  // Players inserted without the API (e.g. in bulk) have no profile until
  // the scraper's first refresh. The rest of the page works without it.
  const profileNotSynced = errorCode === "PROFILE_NOT_SYNCED";
  const notSyncedDetail = axios.isAxiosError<{
    detail?: { name?: string; tracking?: PlayerTracking };
  }>(playerError)
    ? playerError.response?.data?.detail
    : undefined;
  const notSyncedName = notSyncedDetail?.name;
  // Parked before its first profile refresh: nothing loads until it is
  // tracked again, so the page offers that instead of a loading hint.
  const tracking =
    player?.tracking ??
    (profileNotSynced ? notSyncedDetail?.tracking : undefined);
  const isParked = tracking?.state === "parked";
  // Adjusted while rendering, like derived state: another player, the
  // profile confirming the resume, or a new pause after it.
  if (resumed && resumed.tag !== playerTag) setResumed(null);
  else if (resumed && !resumed.confirmed && tracking?.state === "active")
    setResumed({ tag: resumed.tag, confirmed: true });
  else if (resumed?.confirmed && isParked) {
    setResumed(null);
    setNoticeEpisode((episode) => episode + 1);
  }

  // Recorded when the page loads, not when picked in the search, so links and
  // typed tags count too.
  const viewedTag =
    player?.tag ?? (profileNotSynced ? normalizePlayerTag(playerTag) : null);
  const viewedName = player?.name ?? notSyncedName ?? "";
  useEffect(() => {
    if (viewedTag) recordRecentPlayer({ tag: viewedTag, name: viewedName });
  }, [viewedTag, viewedName]);
  // Once per player page, for the explore ranking and the popular badge
  useProfileView(viewedTag);

  // The battles position only serves switching between this player's pages.
  // Leaving the player (home, another player) drops it.
  useEffect(() => () => clearBattlesView(playerTag), [playerTag]);

  // A tag that stopped resolving would only lead back to this error page.
  useEffect(() => {
    const tag = normalizePlayerTag(playerTag);
    if (isNotFound && tag) removeRecentPlayer(tag);
  }, [isNotFound, playerTag]);

  if (playerLoading)
    return <CircularProgress className="layout-loading-spinner" />;
  // A failed refetch keeps the previously loaded profile on screen
  if (isPlayerError && !player && !profileNotSynced) {
    console.log(playerError);

    const displayMessage = isNotFound
      ? "We searched everywhere, but couldn't find the player you were looking for in our system"
      : "Something went wrong while loading the player data. Please try again later.";

    return (
      <div className="layout-error-container">
        {/* Player not found */}
        {isNotFound && (
          <Lottie
            animationData={emptyBox}
            loop={true}
            className="lottie-animation"
          />
        )}
        {/* Generic backend issue */}
        {!isNotFound && (
          <Lottie
            animationData={genericError}
            loop={true}
            className="lottie-animation"
          />
        )}
        <h2 className="layout-error-message">{displayMessage}</h2>
        <NavLink to={`/`} className="nav-link">
          <ChevronLeft />
          Back to Home
        </NavLink>
      </div>
    );
  }

  return (
    <div className="player-layout">
      <nav className="player-nav-container">
        <div className="nav-section nav-home">
          <NavLink to={`/`} className="nav-link">
            <House />
            Home
          </NavLink>
        </div>

        {/* Only show on mobile */}
        <button className="nav-toggle" onClick={() => setMenuOpen((v) => !v)}>
          {menuOpen ? <X /> : <Menu />}
        </button>

        {/* Page menu: Desktop = inline, Mobile = Dropdown */}
        <div
          id="player-nav-menu"
          className={`nav-section nav-pages ${menuOpen ? "is-open" : ""}`}
          role="menu"
        >
          <NavLink
            to={`/player/${encodedTag}/battles`}
            className="nav-link"
            role="menuitem"
          >
            Battles
          </NavLink>
          <NavLink
            to={`/player/${encodedTag}/decks`}
            className="nav-link"
            role="menuitem"
          >
            Decks
          </NavLink>
          <NavLink
            to={`/player/${encodedTag}/cards`}
            className="nav-link"
            role="menuitem"
          >
            Cards
          </NavLink>
          <NavLink
            to={`/player/${encodedTag}/plots`}
            className="nav-link"
            role="menuitem"
          >
            Plots
          </NavLink>
        </div>
      </nav>
      <header className="player-header">
        {/* Stays after "Track again" to show its outcome, until the
            player is left or parked anew */}
        {(isParked || resumed?.tag === playerTag) && (
          <ParkedNotice
            // A new player or a new pause starts without the old outcome
            key={`${playerTag}:${noticeEpisode}`}
            playerTag={playerTag}
            since={tracking?.since}
            onResumed={() => setResumed({ tag: playerTag, confirmed: false })}
          />
        )}
        {/* Only while nobody keeps the player, and also without a profile:
            the leaderboard adds players the scraper refreshes only within
            hours. Stays after "Keep tracking" to show its outcome. A
            missing regular (profiles cached before it) counts as kept. */}
        {tracking?.topFinish &&
          !isParked &&
          (tracking.regular === false || kept) && (
            <TopPlayerNotice
              key={playerTag}
              playerTag={playerTag}
              topFinish={tracking.topFinish}
              onKept={markKept}
            />
          )}
        {player ? (
          <PlayerInfo player={player} />
        ) : (
          profileNotSynced && (
            <PlayerInfoPlaceholder
              tag={playerTag}
              name={notSyncedName}
              parked={isParked}
            />
          )
        )}
      </header>
      <main className="player-content">
        <PlayerErrorBoundary key={pathname}>
          <Outlet /> {/* displays active subpage */}
        </PlayerErrorBoundary>
      </main>
    </div>
  );
}
