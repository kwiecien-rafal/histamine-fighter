import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      // The admin SPA route is /admin; only the API sub-paths are proxied, so a
      // browser load of /admin still falls through to index.html.
      "/api": process.env.VITE_API_PROXY ?? "http://localhost:8000",
      "/admin/auth": process.env.VITE_API_PROXY ?? "http://localhost:8000",
      "/admin/meals": process.env.VITE_API_PROXY ?? "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "src/test/setup.ts",
  },
});
