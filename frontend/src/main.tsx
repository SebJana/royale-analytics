import { StrictMode } from "react";
import { BrowserRouter } from "react-router-dom";
import { createRoot } from "react-dom/client";
import { QueryClient } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { AuthProvider } from "./hooks/useAuth";
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
          // v2: tower troops in the card list and in deck and card statistics
          buster: "v2",
          // Decide wether or not to keep the query progress
          dehydrateOptions: {
            shouldDehydrateQuery: (q) => q.meta?.persist !== false, // skip those with persist:false
          },
        }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </PersistQueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);
