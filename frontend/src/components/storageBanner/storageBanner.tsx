import { useContext } from "react";
import { StorageSettingsContext } from "../../contexts/StorageSettingsContext";
import { useStorageConsent } from "../../hooks/useStorageConsent";
import { setConsent } from "../../utils/storage";
import "./storageBanner.css";

/**
 * Asks once whether preferences may be kept across visits. Shown until the
 * user decides, without blocking the page: before a choice, preferences live
 * only in sessionStorage and end with the tab, so the site works the same
 * while the banner is open.
 */
export function StorageBanner() {
  const consent = useStorageConsent();
  const openStorageSettings = useContext(StorageSettingsContext);

  if (consent) return null;

  return (
    <section className="storage-banner" aria-label="Storage choice">
      <p className="storage-banner-text">
        This site saves a few things in your browser to work. If you accept, it
        also remembers viewed players, filters and player data between visits.
        No tracking.{" "}
        <button
          type="button"
          className="storage-banner-settings"
          onClick={openStorageSettings}
        >
          Settings
        </button>
      </p>
      {/* Equal buttons, so declining is as easy as accepting. */}
      <div className="storage-banner-actions">
        <button type="button" onClick={() => setConsent(false)}>
          Necessary only
        </button>
        <button type="button" onClick={() => setConsent(true)}>
          Accept
        </button>
      </div>
    </section>
  );
}
