import { createContext } from "react";

/**
 * Opens the storage settings dialog, which App renders once. Lets the banner,
 * the footer and the search hint share one dialog.
 */
export const StorageSettingsContext = createContext<() => void>(() => {});
