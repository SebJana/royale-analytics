import { useSyncExternalStore } from "react";
import {
  getConsent,
  subscribeConsent,
  type StorageConsent,
} from "../utils/storage";

/**
 * The user's storage choice, updated when it changes in this or another tab.
 *
 * @returns The choice, or null while the user has not decided yet
 */
export function useStorageConsent(): StorageConsent | null {
  return useSyncExternalStore(subscribeConsent, getConsent);
}
