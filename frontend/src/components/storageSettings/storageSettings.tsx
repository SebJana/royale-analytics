import { useState } from "react";
import {
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Switch,
} from "@mui/material";
import { X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useStorageConsent } from "../../hooks/useStorageConsent";
import {
  STORED_ITEMS,
  clearAllStoredData,
  setConsent,
  type StorageCategory,
} from "../../utils/storage";
import "./storageSettings.css";

function StoredItemTable({
  category,
  labelledBy,
}: Readonly<{ category: StorageCategory; labelledBy: string }>) {
  return (
    <table className="storage-settings-items" aria-labelledby={labelledBy}>
      <thead>
        <tr>
          <th scope="col">What is stored</th>
          <th scope="col">Kept for</th>
        </tr>
      </thead>
      <tbody>
        {STORED_ITEMS.filter((item) => item.category === category).map(
          (item) => (
            <tr key={item.label}>
              <td>{item.label}</td>
              <td>{item.retention}</td>
            </tr>
          ),
        )}
      </tbody>
    </table>
  );
}

/**
 * Shows what the site stores, lets the user grant or revoke preferences, and
 * deletes everything stored in this browser.
 */
export function StorageSettings({
  open,
  onClose,
}: Readonly<{
  open: boolean;
  onClose: () => void;
}>) {
  const consent = useStorageConsent();
  const queryClient = useQueryClient();
  // Deleting also ends a verification, so it takes a second click.
  const [confirmDelete, setConfirmDelete] = useState(false);
  const preferences = consent?.preferences === true;

  const handleClose = () => {
    setConfirmDelete(false);
    onClose();
  };

  const handleDeleteAll = () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    // Cleared first, so no queued cache save writes the old data back.
    queryClient.clear();
    clearAllStoredData();
    // Auth state and caches still live in memory; a reload starts clean and
    // shows the banner again.
    window.location.reload();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      scroll="paper"
      maxWidth="sm"
      fullWidth
      className="storage-settings"
      aria-labelledby="storage-settings-title"
    >
      <DialogTitle id="storage-settings-title">
        <span className="storage-settings-title-row">
          Storage settings
          <button
            type="button"
            className="storage-settings-close"
            aria-label="Close"
            onClick={handleClose}
          >
            <X aria-hidden="true" />
          </button>
        </span>
        <span className="storage-settings-intro">
          Choose what this site may keep in your browser. Nothing is used for
          tracking or shared with anyone.
        </span>
      </DialogTitle>
      <DialogContent>
        <section className="storage-settings-category">
          <header className="storage-settings-category-header">
            <div>
              <h3 id="storage-settings-necessary">Necessary</h3>
              <p>Needed for the site to work, so it can't be turned off.</p>
            </div>
            <div className="storage-settings-state is-locked">
              <span>Always on</span>
              <Switch
                checked
                disabled
                slotProps={{
                  input: { "aria-labelledby": "storage-settings-necessary" },
                }}
              />
            </div>
          </header>
          {/* NOTE Sums up the necessary items in STORED_ITEMS by purpose. No
          consent is asked for them, so their purpose is enough here.
          TODO The privacy policy has to list them with their durations,
          rendered from STORED_ITEMS, so both stay congruent (DSK
          Orientierungshilfe Telemedien Rn. 38). */}
          <p className="storage-settings-summary">
            Your choice here, verification for removing players, and short-lived
            technical data like your scroll position and cached card lists.
          </p>
        </section>

        <section
          className={`storage-settings-category${preferences ? " is-on" : ""}`}
        >
          <header className="storage-settings-category-header">
            <div>
              <h3 id="storage-settings-preferences">Preferences</h3>
              <p>
                Optional. Remembers your choices between visits. When off,
                they're forgotten once you close the tab.
              </p>
            </div>
            <label
              className={`storage-settings-state${preferences ? " is-on" : ""}`}
            >
              {/* The visible state, so on and off do not depend on the
              switch's color alone. */}
              <span aria-hidden="true">{preferences ? "On" : "Off"}</span>
              <Switch
                checked={preferences}
                onChange={(event) => setConsent(event.target.checked)}
                slotProps={{
                  input: { "aria-labelledby": "storage-settings-preferences" },
                }}
              />
            </label>
          </header>
          <StoredItemTable
            category="preferences"
            labelledBy="storage-settings-preferences"
          />
        </section>

        <section className="storage-settings-delete-section">
          <div>
            <h3>Delete all data</h3>
            <p>
              Removes everything this site saved in this browser, including your
              verification and these settings. Tracked players you added stay
              tracked, as they aren't tied to you. The page reloads.
            </p>
          </div>
          <button
            type="button"
            className={`storage-settings-delete${confirmDelete ? " is-confirming" : ""}`}
            onClick={handleDeleteAll}
            aria-live="polite"
          >
            {/* Both labels share one grid cell, so the button keeps the width
            of the longer one and the text beside it does not move. */}
            <span className={confirmDelete ? "is-hidden" : undefined}>
              Delete
            </span>
            <span className={confirmDelete ? undefined : "is-hidden"}>
              Click again to confirm
            </span>
          </button>
        </section>
      </DialogContent>
      <DialogActions>
        <button
          type="button"
          className="storage-settings-done"
          onClick={handleClose}
        >
          Done
        </button>
      </DialogActions>
    </Dialog>
  );
}
