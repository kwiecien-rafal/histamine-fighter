import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": apiTarget,
      // /admin is both an API prefix and the admin SPA route. Proxy the API
      // calls (fetch, sec-fetch-mode cors/same-origin) but let a browser
      // navigation to /admin fall through to the SPA, so new admin endpoints
      // need no proxy edits.
      "/admin": {
        target: apiTarget,
        bypass(req) {
          if (req.headers["sec-fetch-mode"] === "navigate") return "/index.html";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "src/test/setup.ts",
  },
});
