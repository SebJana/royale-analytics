import { useEffect, useState } from "react";
import { Castle, ChevronUp, Info, Shield, Trophy } from "lucide-react";
import { AppTooltip } from "../tooltip/tooltip";
import { CopyableTag } from "../copyableTag/copyableTag";
import { Sticker } from "../sticker/sticker";
import type { Player } from "../../types/player";
import { formatCompactNumber, formatNumber, round } from "../../utils/number";
import {
  formatDateForInput,
  formatTimeAgo,
  getClashRoyaleReleaseDate,
} from "../../utils/datetime";
import { readPreference, writePreference } from "../../utils/storage";
import { StatCard } from "../statCard/statCard";
import "./playerInfo.css";

/**
 * Saves the expanded/collapsed state of the player info details section as a preference.
 * This allows the component to remember the user's preference across page reloads.
 *
 * @param state - The expanded state to save (true = expanded, false = collapsed)
 */
function saveExpandedInfoState(state: boolean): void {
  writePreference("player-info-expanded", String(state));
}

/**
 * Retrieves the saved expanded/collapsed state of the player info details section.
 * Returns the user's previously saved preference, or defaults to expanded (true) if no preference exists.
 *
 * @returns The saved expanded state (true = expanded, false = collapsed)
 */
function getExpandedInfoState(): boolean {
  const state = readPreference("player-info-expanded");

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

/**
 * The note after an untracked period on the battles found for it.
 *
 * @param count - Battles found through other tracked players, null or
 *   undefined when the period predates that record
 * @returns e.g. " (799 on record)", empty when unknown
 */
function passiveCountSuffix(count: number | null | undefined): string {
  if (count == null) return "";
  if (count === 0) return " (none on record)";
  return ` (${count.toLocaleString()} on record)`;
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
  // Accounts from the game's launch year. The creation date is derived from
  // a day count, so an account from the first days of the next year can
  // land here too, which is close enough for a sticker.
  const launchYear = getClashRoyaleReleaseDate().getFullYear();
  const isLaunchYearAccount =
    accountCreationDate?.startsWith(String(launchYear)) ?? false;
  const elapsedTimeSplit = accountCreationDate
    ? getAccountAgeBreakdown(accountCreationDate)
    : null;

  const winPercentage =
    player?.battleCount > 0
      ? round((player?.wins / player?.battleCount) * 100, 1)
      : 0;

  // Only shown when there is something to say about an untracked period
  const hasTrackingHistory =
    !!player?.syncInfo?.battlesBeforeTracking ||
    !!player?.syncInfo?.trackingGaps?.length;

  return (
    <div className="player-info-component-container">
      <div className="player-info-component-header">
        <div className="player-info-component-basic-info">
          <h1 className="player-info-component-name">{player?.name}</h1>
          {player?.popularity && (
            // NOTE: "30 days" matches VIEWS_WINDOW_DAYS in the data
            // scraper's settings. The count is rounded by the backend.
            <AppTooltip
              title="Rank among all tracked players by visitors in the last 30 days."
              tapToShow
            >
              <Sticker
                tone={player.popularity.rank <= 3 ? "gold" : "orange"}
                className="player-info-component-popularity-badge"
              >
                <span className="sticker-figure">
                  #{player.popularity.rank}
                </span>
                <span className="sticker-caption">
                  most viewed
                  <br />
                  {formatCompactNumber(player.popularity.views30d)} views in 30
                  days
                </span>
              </Sticker>
            </AppTooltip>
          )}
          <p className="player-info-component-tag">
            {player?.tag && <CopyableTag tag={player.tag} />}
          </p>
          <p className="player-info-component-clan">
            <Shield
              className="player-info-component-icon player-info-component-clan-icon"
              size={16}
              aria-hidden="true"
            />
            {player?.clan?.name ?? "No Clan"}
          </p>
          <p className="player-info-component-arena">
            <Castle
              className="player-info-component-icon player-info-component-arena-icon"
              size={16}
              aria-hidden="true"
            />
            {player?.arena?.name ?? "No Arena"}
          </p>
          {Boolean(player?.trophies) && (
            <p className="player-info-component-trophies">
              <Trophy
                className="player-info-component-icon player-info-component-trophy-icon"
                size={18}
                aria-hidden="true"
              />
              {formatNumber(player.trophies)}
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
        className={`player-info-component-collapsible-content ${
          !isDetailsExpanded ? "collapsed" : ""
        }`}
      >
        <div className="player-info-component-collapsible-inner">
          <div className="player-info-component-details">
            <div className="player-info-component-account-info-section">
              <h3>Account Information</h3>
              <div className="player-info-component-info-grid">
                <div className="player-info-component-info-item">
                  <span className="player-info-component-info-label">
                    Created On:
                  </span>
                  <span className="player-info-component-info-value">
                    {isLaunchYearAccount && (
                      <AppTooltip
                        title={`Playing since the launch year, ${launchYear}`}
                        tapToShow
                      >
                        <Sticker size="sm" className="player-info-component-og">
                          og
                        </Sticker>
                      </AppTooltip>
                    )}
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
              </div>
            </div>

            {/* Between the two sections, so it follows Account Information on
                narrow screens; wide screens place it below both (CSS). A line
                of text like the sync hint, so one period or many both read
                naturally and nothing competes with the statistics. */}
            {hasTrackingHistory && player?.syncInfo && (
              <p className="player-info-component-tracking-hint">
                <span className="player-info-component-tracking-hint-label">
                  Not tracked
                  <AppTooltip
                    title="Battles from these periods may be missing. Those on record were found through other tracked players they played with or against."
                    tapToShow
                  >
                    <span
                      className="player-info-component-tracking-hint-icon"
                      aria-label="About untracked periods"
                      tabIndex={0}
                    >
                      <Info size={16} />
                    </span>
                  </AppTooltip>
                </span>
                {!!player.syncInfo.battlesBeforeTracking && (
                  <span className="player-info-component-tracking-hint-period">
                    before {player.syncInfo.trackedSince}
                    {passiveCountSuffix(player.syncInfo.battlesBeforeTracking)}
                  </span>
                )}
                {player.syncInfo.trackingGaps?.map((gap) => (
                  <span
                    key={`${gap.from}-${gap.to}`}
                    className="player-info-component-tracking-hint-period"
                  >
                    {gap.from === gap.to
                      ? `${gap.from}, ${gap.hours} h`
                      : `${gap.from} – ${gap.to}`}
                    {passiveCountSuffix(gap.passiveBattles)}
                  </span>
                ))}
              </p>
            )}

            <div className="player-info-component-battle-stats-section">
              <h3>Battle Statistics</h3>
              <div className="player-info-component-stats-grid">
                <StatCard value={player?.wins ?? 0} label="Wins" />
                <StatCard value={player?.losses ?? 0} label="Losses" />
                <StatCard
                  value={player?.battleCount ?? 0}
                  label="Total Battles"
                />
                <StatCard value={`${winPercentage}%`} label="Win Rate" />
                <StatCard
                  value={player?.threeCrownWins ?? 0}
                  label="Three Crown Wins"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
