import { useEffect, useState } from "react";
import { ChevronUp } from "lucide-react";
import type { Player } from "../../types/player";
import { formatNumber, round } from "../../utils/number";
import { formatDateForInput, formatTimeAgo } from "../../utils/datetime";
import { StatCard } from "../statCard/statCard";
import "./playerInfo.css";

/**
 * Saves the expanded/collapsed state of the player info details section to localStorage.
 * This allows the component to remember the user's preference across page reloads.
 *
 * @param state - The expanded state to save (true = expanded, false = collapsed)
 */
function saveExpandedInfoState(state: boolean): void {
  localStorage.setItem("player-info-expanded", String(state));
}

/**
 * Retrieves the saved expanded/collapsed state of the player info details section from localStorage.
 * Returns the user's previously saved preference, or defaults to expanded (true) if no preference exists.
 *
 * @returns The saved expanded state (true = expanded, false = collapsed)
 */
function getExpandedInfoState(): boolean {
  const state = localStorage.getItem("player-info-expanded");

  // Return true (expanded) by default if no state is saved
  if (state === null) {
    return true;
  }

  // Convert string to boolean - only "true" string should return true
  if (state) {
    if (state === "true") {
      return true;
    }
  }

  // When stored value is not "true"
  return false;
}

/**
 * Get the account creation date given an account age in days.
 *
 * @param {number} accountAgeDays - Number of days since the account was created.
 * @returns {string} The calculated account creation date in YYYY-MM-DD format.
 */
function getAccountCreationDate(accountAgeDays: number): string {
  const today = new Date(); // current date & time
  const result = new Date(today); // copy
  result.setDate(result.getDate() - accountAgeDays);
  return formatDateForInput(result);
}

/**
 * Get how many years, weeks, and days have passed since account creation.
 * Does not take leap years into account.
 *
 * @param {string} creationDateStr - Account creation date in YYYY-MM-DD format.
 * @returns {{ years: number, weeks: number, days: number }}
 *   Object with elapsed years, weeks, and days.
 */
function getAccountAgeBreakdown(creationDateStr: string) {
  const creationDate = new Date(creationDateStr);
  const today = new Date();

  // difference in total days
  const diffMs = today.getTime() - creationDate.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  // break down into years, weeks, and days
  const years = Math.floor(diffDays / 365);
  const remainingDaysAfterYears = diffDays % 365;

  const weeks = Math.floor(remainingDaysAfterYears / 7);
  const days = remainingDaysAfterYears % 7;

  return { years, weeks, days };
}

// How often the "updated ... ago" hint is recalculated
const SYNC_HINT_REFRESH_MS = 30_000;

/**
 * Keep the current time in state, updated every intervalMs.
 * Lets relative times like "3 minutes ago" advance without new data.
 *
 * @param intervalMs - Update interval in milliseconds
 * @returns The current time in milliseconds
 */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function PlayerInfo({
  player,
}: Readonly<{
  player: Player;
}>) {
  const [isDetailsExpanded, setIsDetailsExpanded] = useState(
    getExpandedInfoState(),
  );
  const now = useNow(SYNC_HINT_REFRESH_MS);

  // Data is not live: battles are checked every few minutes, the profile
  // about once a day. The hint tells users how old the shown data is.
  const battlesSyncedAgo = formatTimeAgo(
    player?.syncInfo?.battlesSyncedAt,
    now,
  );
  const profileSyncedAgo = formatTimeAgo(
    player?.syncInfo?.profileSyncedAt,
    now,
  );

  // The account age only exists as the YearsPlayed badge (progress = days
  // since creation), which the Clash Royale API does not return for every
  // account. Counting a missing badge as 0 days would show today as the
  // creation date, so both show the same dash as other missing dates.
  const accountAgeDays = player?.badges?.find(
    (b) => b.name === "YearsPlayed",
  )?.progress;

  const accountCreationDate =
    accountAgeDays === undefined
      ? null
      : getAccountCreationDate(accountAgeDays);
  const elapsedTimeSplit = accountCreationDate
    ? getAccountAgeBreakdown(accountCreationDate)
    : null;

  const winPercentage =
    player?.battleCount > 0
      ? round((player?.wins / player?.battleCount) * 100, 1)
      : 0;

  return (
    <div className="player-info-component-container">
      <div className="player-info-component-header">
        <div className="player-info-component-basic-info">
          <h1 className="player-info-component-name">{player?.name}</h1>
          <p className="player-info-component-tag">{player?.tag}</p>
          <p className="player-info-component-clan">
            🛡️ {player?.clan?.name ?? "No Clan"}
          </p>
          <p className="player-info-component-arena">
            🏟️ {player?.arena?.name ?? "No Arena"}
          </p>
          {Boolean(player?.trophies) && (
            <p className="player-info-component-trophies">
              🏆 {formatNumber(player.trophies)}
            </p>
          )}
          {player?.syncInfo && (
            <p className="player-info-component-sync-hint">
              <span>
                {battlesSyncedAgo
                  ? `Battles updated ${battlesSyncedAgo}`
                  : "Battles not synced yet"}
              </span>
              {profileSyncedAgo && (
                // Profile stats are refreshed about once a day, so they can
                // trail the battles shown below by hours.
                <span>
                  Profile stats updated {profileSyncedAgo} (can lag behind)
                </span>
              )}
            </p>
          )}
        </div>
        <button
          type="button"
          className={`player-info-component-section-toggle ${
            !isDetailsExpanded ? "collapsed" : ""
          }`}
          onClick={() => {
            setIsDetailsExpanded(!isDetailsExpanded);
            saveExpandedInfoState(!isDetailsExpanded);
          }}
        >
          <ChevronUp />
        </button>
      </div>

      <div
        className={`player-info-component-collapsible-content player-info-component-details ${
          !isDetailsExpanded ? "collapsed" : ""
        }`}
      >
        <div className="player-info-component-account-info-section">
          <h3>Account Information</h3>
          <div className="player-info-component-info-grid">
            <div className="player-info-component-info-item">
              <span className="player-info-component-info-label">
                Created On:
              </span>
              <span className="player-info-component-info-value">
                {accountCreationDate ?? "—"}
              </span>
            </div>
            <div className="player-info-component-info-item">
              <span className="player-info-component-info-label">
                Account Age:
              </span>
              <span className="player-info-component-info-value">
                {elapsedTimeSplit
                  ? `${elapsedTimeSplit.years}y ${elapsedTimeSplit.weeks}w ${elapsedTimeSplit.days}d`
                  : "—"}
              </span>
            </div>
            {player?.syncInfo?.trackedSince && (
              <div className="player-info-component-info-item">
                <span className="player-info-component-info-label">
                  Tracked Since:
                </span>
                <span className="player-info-component-info-value">
                  {player.syncInfo.trackedSince}
                </span>
              </div>
            )}
            {player?.syncInfo?.trackingGaps?.map((gap) => (
              <p
                key={`${gap.from}-${gap.to}`}
                className="player-info-component-gap-hint"
              >
                {gap.from === gap.to
                  ? `Not tracked on ${gap.from} for ${gap.hours} h`
                  : `Not tracked ${gap.from} – ${gap.to}`}
                , battles from then may be missing
              </p>
            ))}
          </div>
        </div>

        <div className="player-info-component-battle-stats-section">
          <h3>Battle Statistics</h3>
          <div className="player-info-component-stats-grid">
            <StatCard value={player?.wins ?? 0} label="Wins" />
            <StatCard value={player?.losses ?? 0} label="Losses" />
            <StatCard value={player?.battleCount ?? 0} label="Total Battles" />
            <StatCard value={`${winPercentage}%`} label="Win Rate" />
            <StatCard
              value={player?.threeCrownWins ?? 0}
              label="Three Crown Wins"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
