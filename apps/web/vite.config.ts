/// <reference types="vitest/config" />
// The console (console-ui.md §7). In development /api is proxied to the core service
// (uvicorn mothusi_core.main:app on 8000), so the browser talks to one origin and SSE needs no CORS.
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const core = process.env.MOTHUSI_CORE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The demo class set is read straight from services/core/fixtures (one copy of the data).
    fs: { allow: [fileURLToPath(new URL("../..", import.meta.url))] },
    proxy: {
      "/api": { target: core, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
