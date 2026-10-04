import axios from "axios";

// Base URL configuration:
// - Development: Vite proxy handles /api -> local nginx -> API
// - Production (Docker): nginx proxy handles /api -> http://api:8000/api
const api = axios.create({
  baseURL: "/api",
  timeout: 5000,
  headers: {
    "Content-Type": "application/json",
  },
});

// Counts API requests in flight, so background work (card image preloading)
// can step aside while the page waits on its own data.
let pendingRequests = 0;
const idleWaiters = new Set<() => void>();

function settleRequest() {
  pendingRequests = Math.max(0, pendingRequests - 1);
  if (pendingRequests > 0) return;
  for (const resolve of idleWaiters) resolve();
  idleWaiters.clear();
}

api.interceptors.request.use((config) => {
  pendingRequests += 1;
  return config;
});
api.interceptors.response.use(
  (response) => {
    settleRequest();
    return response;
  },
  (error) => {
    settleRequest();
    return Promise.reject(error);
  },
);

/**
 * Resolves once no API request is in flight.
 *
 * @returns A promise that resolves immediately when the API is already idle.
 */
export function whenApiIdle(): Promise<void> {
  if (pendingRequests === 0) return Promise.resolve();
  return new Promise((resolve) => idleWaiters.add(resolve));
}

// TODO: Handle player data route rate limits (HTTP 429): read Retry-After,
// show a clear cooldown message in the affected view, preserve existing data,
// and prevent automatic retries/refetches until the cooldown has passed.
export default api;
