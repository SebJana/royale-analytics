// Writes the self-contained dashboard to dist/. The status server serves only
// these files, so the page loads nothing from outside the scraper.
import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, "dist");

// NOTE The keys are the URLs index.html references below /assets/.
const assets = {
  "dashboard.css": "dashboard.css",
  "dashboard.js": "dashboard.js",
  "uPlot.iife.min.js": "node_modules/uplot/dist/uPlot.iife.min.js",
  "uPlot.min.css": "node_modules/uplot/dist/uPlot.min.css",
  "inter-latin-wght-normal.woff2":
    "node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
};

await rm(dist, { recursive: true, force: true });
await mkdir(join(dist, "assets"), { recursive: true });
for (const page of ["index.html", "api.html"])
  await copyFile(join(root, page), join(dist, page));
for (const [name, source] of Object.entries(assets)) {
  await copyFile(join(root, source), join(dist, "assets", name));
}
console.log(`Dashboard built to ${dist}`);
