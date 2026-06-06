#!/usr/bin/env node
/**
 * Capture Assistant Console with a fresh demo chat (for public screenshots).
 * Run with stack up: node capture_assistant_console.mjs
 */
import { chromium } from "@playwright/test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "..", "..", "..", "..", "screenshots");
const BASE = process.env.PLAYWRIGHT_BASE_URL || "https://127.0.0.1:7860";
fs.mkdirSync(OUT, { recursive: true });

const DEMO_SESSION_ID = "sess_screenshot_demo";
const now = Date.now();

const DEMO_MESSAGES = [
  {
    role: "user",
    content: "What's running on my GPU right now?",
    ts: now - 180_000,
  },
  {
    role: "assistant",
    content:
      "You're on an RTX 5090 with Gemma 4 26B for dialogue, Nemotron streaming ASR, and Coqui XTTS — all local. Check the GPU Live card for live VRAM, power, and temperature.",
    ts: now - 175_000,
  },
  {
    role: "user",
    content: "Can you use my documents and camera together?",
    ts: now - 120_000,
  },
  {
    role: "assistant",
    content:
      "Yes. Enable Local RAG to query your offline index, turn on the camera for vision captions, and use Voice+Video to send mic audio and JPEG frames in one session.",
    ts: now - 115_000,
  },
  {
    role: "user",
    content: "Summarize the voice pipeline in one line.",
    ts: now - 60_000,
  },
  {
    role: "assistant",
    content:
      "Mic → Nemotron Speech ASR → Gemma 4 → XTTS → speakers, with Pipecat/WebRTC handling real-time transport.",
    ts: now - 55_000,
  },
];

const DEMO_SESSION_PAYLOAD = {
  version: 1,
  activeId: DEMO_SESSION_ID,
  updatedAt: now,
  sessions: [
    {
      id: DEMO_SESSION_ID,
      name: "Demo",
      createdAt: now - 300_000,
      updatedAt: now,
      messages: DEMO_MESSAGES,
      settings: { ragEnabled: true },
    },
  ],
};

async function seedDemoChatOnServer(request) {
  const res = await request.put(`${BASE}/api/assistant-console/chat-sessions`, {
    data: DEMO_SESSION_PAYLOAD,
    ignoreHTTPSErrors: true,
  });
  if (!res.ok()) {
    throw new Error(`Failed to seed demo chat: HTTP ${res.status()}`);
  }
}

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

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
await seedDemoChatOnServer(ctx.request);

await ctx.addInitScript(() => {
  localStorage.removeItem("assistantConsole.sessions.v1");
  localStorage.removeItem("assistantConsole.activeSession.v1");
});

const page = await ctx.newPage();
await page.goto(`${BASE}/assistant-console`, { waitUntil: "load", timeout: 90000 });
await page.waitForTimeout(5000);
await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
await page.waitForTimeout(800);

// Ensure demo messages rendered (server sync + session apply).
await page.waitForFunction(() => {
  const log = document.getElementById("log");
  return log && log.querySelectorAll(".msg").length >= 4;
}, { timeout: 20000 });

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
