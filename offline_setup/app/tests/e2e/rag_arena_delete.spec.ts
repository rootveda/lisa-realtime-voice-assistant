import { expect, test } from "@playwright/test";

const ADMIN_TOKEN = process.env.LISA_ADMIN_TOKEN || "";
const live = !!process.env.PLAYWRIGHT_BASE_URL;

test.describe("RAG Arena delete (live admin mode)", () => {
  test.skip(!live || !ADMIN_TOKEN, "needs PLAYWRIGHT_BASE_URL and LISA_ADMIN_TOKEN");

  test("delete row with token — file disappears from table", async ({ page }) => {
    await page.goto("/rag-arena");
    await page.evaluate((tok) => {
      localStorage.setItem("assistantConsole.lisaAdminToken.v1", tok);
    }, ADMIN_TOKEN);
    await page.fill("#arenaAdminTokenInputToolbar", ADMIN_TOKEN);
    await page.waitForSelector(".arena-delete-btn", { timeout: 60_000 });

    const firstBtn = page.locator(".arena-delete-btn").first();
    const path = await firstBtn.getAttribute("data-path");
    expect(path).toBeTruthy();

    await firstBtn.click();
    await page.locator("#arenaConfirmOkBtn").click();

    await expect(page.locator("#arenaMsg")).toContainText(/Removed:/i, { timeout: 15_000 });
    await expect(page.locator(`.arena-delete-btn[data-path="${path}"]`)).toHaveCount(0, { timeout: 10_000 });
  });

  test("accepts export line paste in toolbar token field", async ({ page }) => {
    await page.goto("/rag-arena");
    await page.evaluate(() => localStorage.removeItem("assistantConsole.lisaAdminToken.v1"));
    await page.waitForSelector("#arenaAdminTokenInputToolbar", { timeout: 60_000 });
    await page.fill(
      "#arenaAdminTokenInputToolbar",
      `export LISA_ADMIN_TOKEN=${ADMIN_TOKEN}\n`
    );
    await page.waitForSelector(".arena-delete-btn", { timeout: 60_000 });
    await page.locator(".arena-delete-btn").first().click();
    await expect(page.locator("#lisaNoticeOverlay.show")).toHaveCount(0);
    await expect(page.locator("#arenaConfirm")).toHaveClass(/show/);
  });

  test("without token shows auth error modal", async ({ page }) => {
    await page.goto("/rag-arena");
    await page.evaluate(() => localStorage.removeItem("assistantConsole.lisaAdminToken.v1"));
    await page.fill("#arenaAdminTokenInputToolbar", "");
    await page.fill("#arenaAdminTokenInput", "");
    await page.waitForSelector(".arena-delete-btn", { timeout: 60_000 });
    await page.locator(".arena-delete-btn").first().click();
    await expect(page.locator("#lisaNoticeOverlay.show")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#lisaNoticeTitle")).toContainText(/Unable to delete/i);
  });
});
