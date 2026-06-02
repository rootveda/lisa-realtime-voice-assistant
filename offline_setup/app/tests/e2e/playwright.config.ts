import { defineConfig } from "@playwright/test";
import path from "path";

/** From tests/e2e → ../../static */
const staticRoot = path.resolve(process.cwd(), "..", "..", "static");

/** Serve app/static so harness loads without the full bot. */
const staticPort = process.env.PLAYWRIGHT_STATIC_PORT || "47999";
const useExternal =
  typeof process.env.PLAYWRIGHT_BASE_URL === "string" &&
  process.env.PLAYWRIGHT_BASE_URL.length > 0;

const regressionSuite = process.env.REGRESSION_SUITE === "1";

export default defineConfig({
  testDir: ".",
  timeout: regressionSuite ? 480_000 : 30_000,
  globalTimeout: regressionSuite ? 1_200_000 : undefined,
  ...(useExternal
    ? {}
    : {
        webServer: {
          command: `python3 -m http.server ${staticPort} --bind 127.0.0.1 --directory "${staticRoot}"`,
          url: `http://127.0.0.1:${staticPort}/session_memory/face_phase_harness.html`,
          reuseExistingServer: true,
        },
      }),
  use: {
    baseURL: useExternal ? process.env.PLAYWRIGHT_BASE_URL! : `http://127.0.0.1:${staticPort}`,
    ignoreHTTPSErrors:
      !!useExternal && process.env.PLAYWRIGHT_BASE_URL!.startsWith("https:"),
  },
});
