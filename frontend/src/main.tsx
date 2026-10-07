import { StrictMode } from "react";
import { BrowserRouter } from "react-router-dom";
import { createRoot } from "react-dom/client";
import { QueryClient, type Query } from "@tanstack/react-query";
import {
  PersistQueryClientProvider,
  persistQueryClientSave,
} from "@tanstack/react-query-persist-client";
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { AuthProvider } from "./hooks/useAuth";
import {
  NECESSARY_QUERY_META,
  PREFERENCE_QUERY_META,
  hasPreferenceConsent,
  subscribeConsent,
} from "./utils/storage";
import "./index.css";
import App from "./App.tsx";

const day = 24 * 60 * 60 * 1000;
const min = 60 * 1000;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      gcTime: day,
      staleTime: min,
      refetchOnWindowFocus: false,
      retry: 1, // Reduce retries to prevent long operations
      retryDelay: 1000, // Short retry delay
    },
    mutations: {
      retry: 1,
    },
  },
});

// Create persister at the root (browser only)
const persister = createAsyncStoragePersister({
  storage: typeof window !== "undefined" ? window.localStorage : undefined,
  key: "rq-cache-v1",
  throttleTime: 2000, // throttle time to reduce frequent saves
});

// Change whenever a cached API response changes shape, so browsers drop the
// saved cache instead of rendering the old shape for up to maxAge.
// Also changed to drop caches saved before the allowlist below, which could
// hold player data without consent.
const buster = "2026-10-07";

const dehydrateOptions = {
  // Allowlist: shared catalogues always, player data only with preference
  // consent (utils/storage.ts). Unmarked queries are never saved.
  shouldDehydrateQuery: (q: Query) =>
    q.meta?.storage === NECESSARY_QUERY_META.storage ||
    (q.meta?.storage === PREFERENCE_QUERY_META.storage &&
      hasPreferenceConsent()),
};

// The cache is otherwise only saved after a query changes, so revoking consent
// would leave player data on the device until then.
subscribeConsent(() => {
  void persistQueryClientSave({
    queryClient,
    persister,
    buster,
    dehydrateOptions,
  });
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <PersistQueryClientProvider
        client={queryClient}
        persistOptions={{
          persister,
          // NOTE: The data scraper keeps replaced card image sets for
          // CARD_IMAGE_SET_RETENTION (7 days) as a grace period for saved card
          // lists. Keep this well below it.
          maxAge: day,
          buster,
          dehydrateOptions,
        }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </PersistQueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);
