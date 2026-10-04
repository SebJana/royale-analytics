import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // A second device can use the host's LAN IP while Vite still forwards API
    // and calibration WebSocket requests through the Docker frontend proxy.
    host: "0.0.0.0",
    proxy: {
      "/api": {
        target: "http://localhost:80",
        changeOrigin: false,
        ws: true,
      },
      // NOTE: Matches CARD_IMAGES_URL_PREFIX of the data scraper.
      "/card-images": {
        target: "http://localhost:80",
        changeOrigin: false,
      },
    },
  },
});
