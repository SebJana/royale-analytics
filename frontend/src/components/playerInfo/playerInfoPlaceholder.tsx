import "./playerInfo.css";

/**
 * Header shown while a player has no stored profile yet.
 *
 * Only players inserted without the API (e.g. in bulk) reach this, until
 * the data scraper's first profile refresh for them. Battles and statistics
 * below work without the profile. A parked player gets no refresh until it
 * is tracked again, so it shows no loading hint.
 */
export function PlayerInfoPlaceholder({
  tag,
  name,
  parked = false,
}: Readonly<{
  tag: string;
  name?: string;
  parked?: boolean;
}>) {
  return (
    <div className="player-info-component-container player-info-placeholder">
      <div className="player-info-component-basic-info">
        <h1 className="player-info-component-name">{name || "Player"}</h1>
        <p className="player-info-component-tag">{tag}</p>
        <p className="player-info-component-sync-hint">
          {parked
            ? "No profile stats stored for this player"
            : "Profile stats are being loaded, this can take a few minutes"}
        </p>
      </div>
    </div>
  );
}
