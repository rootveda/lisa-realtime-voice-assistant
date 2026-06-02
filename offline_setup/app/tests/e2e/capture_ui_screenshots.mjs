#!/usr/bin/env node
/**
 * Capture full-page screenshots of Lisa UI pages with all <details> expanded.
 * Run from offline_setup/app/tests/e2e:
 *   PLAYWRIGHT_BASE_URL=https://127.0.0.1:7860 node capture_ui_screenshots.mjs
 */
import { chromium } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const OUT_DIR = path.join(REPO_ROOT, "screenshots");

const BASE = process.env.PLAYWRIGHT_BASE_URL || "https://127.0.0.1:7860";

const PAGES = [
  { slug: "01-assistant-console", path: "/assistant-console", expand: true, scrollSidebar: true },
  { slug: "03-rag-arena", path: "/rag-arena", expand: true },
  { slug: "04-rag-flow", path: "/rag-flow", expand: true },
  { slug: "05-session-manager", path: "/session-manager", expand: true },
  { slug: "06-instructions-manager", path: "/instructions-manager", expand: true },
  { slug: "07-face-manager", path: "/face-manager", expand: true },
  { slug: "08-diagnose", path: "/diagnose", expand: true },
  { slug: "09-mobile-voice-test", path: "/mobile-voice-test", expand: true },
  { slug: "10-mobile-voice-vision-test", path: "/mobile-voice-vision-test", expand: true },
  { slug: "11-architecture-flow", path: "/architecture-flow", expand: true },
  { slug: "12-client-playground", path: "/client", expand: false },
];

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
            node.placeholder = "Admin token (paste locally — never commit)";
          }
        }
        for (const c of node.childNodes) walk(c);
      }
    };
    walk(document.body);
  });
}

async function expandAll(page) {
  await page.evaluate(() => {
    document.querySelectorAll("details").forEach((d) => {
      d.open = true;
    });
  });
}

async function fillAdminToken(page) {
  const token = process.env.LISA_ADMIN_TOKEN;
  if (!token) return;
  const selectors = [
    'input[placeholder*="lisa_admin_token"]',
    'input[placeholder*="Admin token"]',
    "#adminToken",
    'input[id*="admin" i]',
    'input[name*="admin" i]',
  ];
  for (const sel of selectors) {
    const el = page.locator(sel).first();
    if ((await el.count()) > 0) {
      await el.fill(token);
      break;
    }
  }
}

async function scrollSidebarSections(page) {
  const scrollEl = page.locator(".controls").first();
  if ((await scrollEl.count()) === 0) return [];
  const maxScroll = await scrollEl.evaluate((el) => el.scrollHeight - el.clientHeight);
  const steps = Math.max(1, Math.ceil(maxScroll / 350));
  const positions = [];
  for (let i = 0; i <= steps; i++) {
    const y = Math.min(i * 350, maxScroll);
    await scrollEl.evaluate((el, top) => {
      el.scrollTop = top;
    }, y);
    await page.waitForTimeout(250);
    positions.push(y);
  }
  return positions;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  for (const spec of PAGES) {
    const url = `${BASE}${spec.path}`;
    console.log(`Capturing ${url} ...`);
    try {
      const resp = await page.goto(url, { waitUntil: "networkidle", timeout: 120_000 });
      if (!resp || resp.status() >= 400) {
        console.warn(`  WARN: ${spec.path} status ${resp?.status()}`);
      }
      await page.waitForTimeout(2000);
      if (spec.adminToken) await fillAdminToken(page);
      if (spec.expand) await expandAll(page);
      await sanitizeDomForScreenshots(page);
      await page.waitForTimeout(600);

      const mainFile = path.join(OUT_DIR, `${spec.slug}.png`);
      await page.screenshot({ path: mainFile, fullPage: true });
      console.log(`  -> ${mainFile}`);

      if (spec.scrollSidebar) {
        const positions = await scrollSidebarSections(page);
        for (let i = 0; i < positions.length; i++) {
          const part = path.join(
            OUT_DIR,
            `${spec.slug}-sidebar-${String(i + 1).padStart(2, "0")}.png`,
          );
          await page.screenshot({ path: part, fullPage: false });
          console.log(`  -> ${part}`);
        }
      }
    } catch (err) {
      console.error(`  FAIL ${spec.path}:`, err.message);
    }
  }

  await browser.close();
  console.log("Done.");
}

main();
