#!/usr/bin/env node
/** Re-capture pages that may show local paths or admin tokens (sanitized). */
import { chromium } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "..", "..", "..", "..", "screenshots");
const BASE = process.env.PLAYWRIGHT_BASE_URL || "https://127.0.0.1:7860";

const PAGES = [
  { slug: "03-rag-arena", path: "/rag-arena" },
  { slug: "06-instructions-manager", path: "/instructions-manager" },
  { slug: "07-face-manager", path: "/face-manager" },
  { slug: "01-assistant-console", path: "/assistant-console" },
];

async function sanitize(page) {
  await page.evaluate(() => {
    const reps = [
      [/\/home\/[^/\s<]+[^\s<]*/g, "/path/to/lisa/nvidia_voice"],
      [/backup_[0-9a-z_]+_nvidia_voice/g, "lisa/nvidia_voice"],
      [/192\.168\.\d+\.\d+/g, "<LAN-IP>"],
    ];
    const walk = (n) => {
      if (n.nodeType === 3) {
        let t = n.textContent;
        for (const [r, s] of reps) t = t.replace(r, s);
        if (t !== n.textContent) n.textContent = t;
      } else if (n.nodeType === 1) {
        if (n.tagName === "INPUT" && /token/i.test((n.id || "") + (n.placeholder || ""))) {
          n.value = "";
          n.placeholder = "Admin token (paste locally — never commit)";
        }
        for (const c of n.childNodes) walk(c);
      }
    };
    walk(document.body);
  });
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

for (const { slug, path: p } of PAGES) {
  await page.goto(`${BASE}${p}`, { waitUntil: "load", timeout: 90000 });
  await page.waitForTimeout(2500);
  await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
  await sanitize(page);
  await page.screenshot({ path: `${OUT}/${slug}.png`, fullPage: true });
  console.log("OK", slug);
}

await browser.close();
