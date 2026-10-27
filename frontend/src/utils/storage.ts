/**
 * Everything this site keeps in the browser, and the consent rule for it.
 *
 * Storage on the user's device needs consent unless it is strictly necessary
 * for what the user asked for (§ 25 TDDDG, Art. 5(3) ePrivacy), and that
 * applies to localStorage the same as to cookies. The rule used here: anything
 * that outlives the tab and is not needed for verification or caching shared
 * catalogues is a preference. Without consent, preferences fall back to
 * sessionStorage, so they still work until the tab closes.
 *
 * Preference values are only written through this module, which keeps the
 * consent check in one place instead of at every call site.
 */

export type StorageCategory = "necessary" | "preferences";

export type StoredItem = {
  label: string;
  retention: string;
  category: StorageCategory;
  // Keys owned by this item. Empty when the data lives inside another key
  // (the query cache) or under per-player keys.
  keys: readonly string[];
};

// NOTE List every key the site stores. The storage settings dialog shows the
// preferences as a table and sums up the necessary items in one sentence
// (storageSettings.tsx), and preference keys are moved or deleted by this
// list when consent changes.
export const STORED_ITEMS = [
  {
    label: "Your choice on this page",
    retention: "Until changed",
    category: "necessary",
    keys: ["storageConsent"],
  },
  {
    label: "Verification to remove players",
    retention: "30 min",
    category: "necessary",
    keys: ["clash_royale_remove_player_token"],
  },
  {
    label: "Scroll position in battles",
    retention: "Until you leave the player",
    category: "necessary",
    // battlesView:<tag> in sessionStorage (utils/battlesView.ts)
    keys: [],
  },
  {
    label: "Card, mode and season lists",
    retention: "24 h",
    category: "necessary",
    keys: ["rq-cache-v1"],
  },
  {
    label: "Recently viewed players",
    retention: "Last 10, up to 90 days",
    category: "preferences",
    keys: ["recentPlayers"],
  },
  {
    label: "Filters on the player page",
    retention: "7 days",
    category: "preferences",
    keys: ["filterState", "filterStateLastUpdated"],
  },
  {
    label: "Player details open or closed",
    retention: "Until changed",
    category: "preferences",
    keys: ["player-info-expanded"],
  },
  {
    label: "Player data, for faster reloads",
    retention: "24 h",
    category: "preferences",
    // Persisted inside rq-cache-v1 only with consent (main.tsx)
    keys: [],
  },
] as const satisfies readonly StoredItem[];

export type PreferenceKey = Extract<
  (typeof STORED_ITEMS)[number],
  { category: "preferences" }
>["keys"][number];

const PREFERENCE_KEYS: readonly PreferenceKey[] = STORED_ITEMS.flatMap(
  (item) => (item.category === "preferences" ? item.keys : []),
);

/**
 * Query meta for shared catalogues (cards, game modes, seasons). The persisted
 * query cache keeps only queries marked with this or PREFERENCE_QUERY_META, so
 * an unmarked query is never written to the device.
 */
export const NECESSARY_QUERY_META = { storage: "necessary" } as const;

/**
 * Query meta for player-specific data. The persisted query cache keeps such
 * queries only with preference consent, since the cached player tags amount
 * to a viewing history.
 */
export const PREFERENCE_QUERY_META = { storage: "preferences" } as const;

const CONSENT_KEY = "storageConsent";
// Raise when a new preference is added, so every visitor is asked again.
const CONSENT_VERSION = 1;

export type StorageConsent = {
  preferences: boolean;
  decidedAt: number;
};

type StorageName = "localStorage" | "sessionStorage";

// Accessing window.localStorage itself throws when site data is blocked.
function getStorage(name: StorageName): Storage | null {
  try {
    return window[name];
  } catch {
    return null;
  }
}

function loadConsent(): StorageConsent | null {
  try {
    const raw = getStorage("localStorage")?.getItem(CONSENT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      parsed?.version !== CONSENT_VERSION ||
      typeof parsed.preferences !== "boolean"
    )
      return null;
    return { preferences: parsed.preferences, decidedAt: parsed.decidedAt };
  } catch {
    return null;
  }
}

// Kept in memory, so a choice still holds for this page load when storage is
// blocked, and useSyncExternalStore gets a stable snapshot.
let consent = loadConsent();
const consentListeners = new Set<() => void>();

function notifyConsent() {
  for (const listener of consentListeners) listener();
}

// Another tab changed or cleared the choice; clear() reports a null key.
window.addEventListener("storage", (event) => {
  if (event.key !== CONSENT_KEY && event.key !== null) return;
  consent = loadConsent();
  notifyConsent();
});

/**
 * The user's storage choice.
 *
 * @returns The choice, or null while the user has not decided yet
 */
export function getConsent(): StorageConsent | null {
  return consent;
}

/**
 * Whether preferences may be kept across visits.
 *
 * @returns True only after the user accepted preferences
 */
export function hasPreferenceConsent(): boolean {
  return consent?.preferences === true;
}

/**
 * Subscribe to changes of the storage choice, also from other tabs.
 *
 * @param listener - Called after every change
 * @returns A function that unsubscribes
 */
export function subscribeConsent(listener: () => void): () => void {
  consentListeners.add(listener);
  return () => consentListeners.delete(listener);
}

/**
 * Record the user's storage choice and move stored preferences to match it.
 * Granting moves this tab's preferences into localStorage. Declining or
 * revoking moves them out of localStorage into sessionStorage, so they last
 * until the tab closes and nothing outlives it.
 *
 * @param preferences - Whether preferences may be kept across visits
 */
export function setConsent(preferences: boolean): void {
  consent = { preferences, decidedAt: Date.now() };
  try {
    getStorage("localStorage")?.setItem(
      CONSENT_KEY,
      JSON.stringify({ version: CONSENT_VERSION, ...consent }),
    );
  } catch {
    // The in-memory choice still applies until the page reloads
  }
  movePreferences(
    preferences ? "sessionStorage" : "localStorage",
    preferences ? "localStorage" : "sessionStorage",
  );
  notifyConsent();
}

function movePreferences(fromName: StorageName, toName: StorageName) {
  const from = getStorage(fromName);
  const to = getStorage(toName);
  for (const key of PREFERENCE_KEYS) {
    try {
      const value = from?.getItem(key);
      if (value != null) to?.setItem(key, value);
    } catch {
      // A full or blocked target only loses this one preference
    }
    // Separate, so a failed copy never keeps the old one: on revoke, the
    // localStorage copy has to go either way.
    try {
      from?.removeItem(key);
    } catch {
      // Blocked storage holds nothing to remove
    }
  }
}

// While the user has not decided, localStorage is neither read nor written for
// preferences. Values saved before consent existed stay untouched until then.
function preferenceStorage(): Storage | null {
  return getStorage(hasPreferenceConsent() ? "localStorage" : "sessionStorage");
}

/**
 * Read a preference from where the current consent keeps it.
 *
 * @param key - A preference key from STORED_ITEMS
 * @returns The stored string, or null if none is stored or storage is blocked
 */
export function readPreference(key: PreferenceKey): string | null {
  try {
    return preferenceStorage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/**
 * Store a preference: across visits with consent, until the tab closes
 * without it.
 *
 * @param key - A preference key from STORED_ITEMS
 * @param value - The string to store
 */
export function writePreference(key: PreferenceKey, value: string): void {
  try {
    preferenceStorage()?.setItem(key, value);
  } catch {
    // Blocked or full storage only costs the remembered preference
  }
}

/**
 * Delete a preference from where the current consent keeps it.
 *
 * @param key - A preference key from STORED_ITEMS
 */
export function removePreference(key: PreferenceKey): void {
  try {
    preferenceStorage()?.removeItem(key);
  } catch {
    // Nothing stored that could be removed
  }
}

/**
 * Delete everything this site stored in the browser, necessary items and the
 * storage choice included. In-memory state (auth, query cache) is untouched,
 * so the caller reloads the page afterwards.
 */
export function clearAllStoredData(): void {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    try {
      getStorage(name)?.clear();
    } catch {
      // Blocked storage holds nothing to clear
    }
  }
}
