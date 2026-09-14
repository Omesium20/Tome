import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
// Imported from "vitest/config" rather than "vite" so the `test` block below
// type-checks — Vite's own defineConfig doesn't know about it.
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirrors the "@/*" path alias in tsconfig.json — both have to be kept in
    // sync, tsconfig for the type-checker and this one for the bundler.
    alias: {
      // import.meta.url rather than __dirname — this config is ESM.
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Vite defaults to 5173; 3000 keeps parity with the port the app has always
    // run on and with the mapping in Dockercompose.yaml.
    port: 3000,
    watch: {
      // Bind mounts into Docker (Dockercompose.dev.yaml) don't propagate native
      // filesystem change events on Windows, so HMR silently never fires unless
      // the watcher polls instead. Left off for local `npm run dev`, where native
      // events work fine and polling would just burn CPU.
      usePolling: process.env.DOCKER_DEV === "true",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
