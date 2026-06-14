import { execSync } from "node:child_process";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

// The price table in src/lib/pricing.ts is hand-curated, so its "last updated"
// date is its last git commit date. A dirty working tree means prices are being
// edited right now, so today is the honest date. Falls back to today if git is
// unavailable (e.g. a tarball install).
const PRICING_FILE = "src/lib/pricing.ts";

function pricesUpdated(): string {
  const today = new Date().toISOString().slice(0, 10);
  try {
    const dirty = execSync(`git status --porcelain ${PRICING_FILE}`, {
      encoding: "utf8",
    }).trim();
    if (dirty) return today;
    const committed = execSync(`git log -1 --format=%cs -- ${PRICING_FILE}`, {
      encoding: "utf8",
    }).trim();
    return committed || today;
  } catch {
    return today;
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __PRICES_UPDATED__: JSON.stringify(pricesUpdated()),
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": process.env.VITE_API_PROXY ?? "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "src/test/setup.ts",
  },
});
