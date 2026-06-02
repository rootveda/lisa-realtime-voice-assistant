import { test, expect } from "@playwright/test";

function textChatErrorHarnessPath(): string {
  return process.env.PLAYWRIGHT_BASE_URL && process.env.PLAYWRIGHT_BASE_URL.length > 0
    ? "/session-memory/text_chat_error_harness.html"
    : "/session_memory/text_chat_error_harness.html";
}

/**
 * Mocks the same endpoints the Assistant uses for text chat + attachments.
 * Ensures fetchHttpErrorDetail (session_memory/http_error_detail.js) surfaces JSON error bodies on 400.
 */
test("completions 400 includes JSON error message in detail string", async ({ page }) => {
  await page.route("**/api/text-chat/completions", async (route) => {
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        error: "connector URL not allowed for rag_url (Playwright mock)",
      }),
    });
  });

  await page.goto(textChatErrorHarnessPath());
  await page.getByRole("button", { name: "Run completions error test" }).click();
  await expect(page.locator("#result")).toContainText("connector URL not allowed");
  await expect(page.locator("#result")).toContainText("HTTP 400");
});

test("attachments 400 includes JSON error message", async ({ page }) => {
  await page.route("**/api/chat/attachments", async (route) => {
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, error: "multipart parse failed (Playwright mock)" }),
    });
  });

  await page.goto(textChatErrorHarnessPath());
  await page.getByRole("button", { name: "Run attachments error test" }).click();
  await expect(page.locator("#result")).toContainText("multipart parse failed");
  await expect(page.locator("#result")).toContainText("HTTP 400");
});
