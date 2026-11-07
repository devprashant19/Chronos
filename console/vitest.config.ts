import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts so the dev server config and the test config
// cannot drift into each other. jsdom rather than a real browser: these tests
// assert what the components render, not how a browser paints it. What a
// browser does with it is covered by the screenshots in docs/screenshots/.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
