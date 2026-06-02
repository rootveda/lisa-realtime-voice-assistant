import { test, expect } from "@playwright/test";

/**
 * Loads static harness that runs computeFaceSessionPhase() assertions in-browser.
 * Requires the bot (or any server that mounts /session-memory/* from offline_setup/app/static).
 *
 *   cd offline_setup/app/tests/e2e && npm install && npx playwright install chromium
 *   PLAYWRIGHT_BASE_URL=https://127.0.0.1:7860 npm test
 */
test("face phase harness: no multiface when strict frame is single enrolled", async ({
  page,
}) => {
  const path =
    process.env.PLAYWRIGHT_BASE_URL &&
    process.env.PLAYWRIGHT_BASE_URL.length > 0
      ? "/session-memory/face_phase_harness.html"
      : "/session_memory/face_phase_harness.html";
  await page.goto(path);
  await expect(page.locator("#status")).toHaveText("OK", { timeout: 15_000 });
  const ok = await page.evaluate(() => (window as unknown as { __FACE_PHASE_TEST_OK?: boolean }).__FACE_PHASE_TEST_OK);
  expect(ok).toBe(true);
});
