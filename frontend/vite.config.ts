import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // esbuild keeps every top-level name of an ES module chunk, which in one
    // bundle is every module-level function and constant. Terser renames them
    // with toplevel; the names a chunk exports stay. License comments stay,
    // as the licenses require.
    // NOTE Set per step: Vite overwrites a plain terserOptions.toplevel with
    // false for ES output.
    minify: "terser",
    terserOptions: {
      mangle: { toplevel: true },
      compress: { toplevel: true },
    },
    rollupOptions: {
      output: {
        // lottie-web runs animation expressions through a direct eval, which
        // can reach every name of its chunk, so terser renames none of them.
        // In a chunk of its own it only holds back lottie's names.
        manualChunks: (id) =>
          id.includes("node_modules/lottie-web/") ? "lottie" : undefined,
      },
    },
  },
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
