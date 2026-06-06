#!/usr/bin/env node
import { chromium } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "..", "..", "..", "..", "screenshots");
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto("https://127.0.0.1:7860/assistant-console", { waitUntil: "load", timeout: 90000 });
await page.waitForTimeout(4000);
await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
await page.waitForTimeout(800);

async function sanitizeDomForScreenshots(page) {
  await page.evaluate(() => {
    const replacers = [
      [/\/home\/[^/\s<]+[^\s<]*/g, "/path/to/lisa/nvidia_voice"],
      [/backup_[0-9a-z_]+_nvidia_voice/g, "lisa/nvidia_voice"],
      [/192\.168\.\d+\.\d+/g, "<LAN-IP>"],
    ];
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE && node.textContent) {
        let t = node.textContent;
        for (const [re, sub] of replacers) t = t.replace(re, sub);
        if (t !== node.textContent) node.textContent = t;
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.tagName === "INPUT" || node.tagName === "TEXTAREA") {
          const hint = (node.id || "") + (node.name || "") + (node.getAttribute("placeholder") || "");
          if (/token/i.test(hint)) {
            node.value = "";
            node.placeholder = "Paste token from offline_setup/lisa_admin_token.env";
          }
        }
        for (const c of node.childNodes) walk(c);
      }
    };
    walk(document.body);
  });
}

await sanitizeDomForScreenshots(page);
await page.waitForTimeout(300);
await page.screenshot({ path: path.join(OUT, "01-assistant-console.png"), fullPage: true });
const scrollEl = page.locator(".controls").first();
if (await scrollEl.count()) {
  const max = await scrollEl.evaluate((el) => el.scrollHeight - el.clientHeight);
  let i = 0;
  for (let y = 0; y <= max; y += 320) {
    await scrollEl.evaluate((el, t) => { el.scrollTop = t; }, y);
    await page.waitForTimeout(250);
    await page.screenshot({
      path: path.join(OUT, `01-assistant-console-sidebar-${String(++i).padStart(2, "0")}.png`),
    });
  }
}
await browser.close();
console.log("OK", OUT);
