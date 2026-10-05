import { Outlet, NavLink, useParams, useLocation } from "react-router-dom";
import {
  usePlayerProfile,
  getProfileErrorCode,
} from "../../hooks/usePlayerProfile";
import { House, Menu, X, ChevronLeft } from "lucide-react";
import { PlayerInfo } from "../../components/playerInfo/playerInfo";
import { PlayerInfoPlaceholder } from "../../components/playerInfo/playerInfoPlaceholder";
import { PlayerErrorBoundary } from "../../components/playerError/playerErrorBoundary";
import { useEffect, useState } from "react";
import axios from "axios";
import Lottie from "lottie-react";
import emptyBox from "../../assets/animations/emptyBox.json";
import genericError from "../../assets/animations/404.json";

import CircularProgress from "@mui/material/CircularProgress";
import "./layout.css";

export default function PlayerLayout() {
  const { playerTag = "" } = useParams();
  const [menuOpen, setMenuOpen] = useState(false);
  const encodedTag = encodeURIComponent(playerTag ?? "");
  const { pathname } = useLocation();

  // Close menu after navigation
  useEffect(() => setMenuOpen(false), [pathname]);

  const {
    data: player,
    isLoading: playerLoading,
    isError: isPlayerError,
    error: playerError,
  } = usePlayerProfile(playerTag ?? "");

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
  const notSyncedName = axios.isAxiosError<{ detail?: { name?: string } }>(
    playerError,
  )
    ? playerError.response?.data?.detail?.name
    : undefined;

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
        {player ? (
          <PlayerInfo player={player} />
        ) : (
          profileNotSynced && (
            <PlayerInfoPlaceholder tag={playerTag} name={notSyncedName} />
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
