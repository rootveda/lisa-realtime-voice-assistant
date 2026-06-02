/**
 * Full-stack regression against a running bot (Lisa offline stack).
 *
 * Prerequisite: start the stack, then:
 *   cd offline_setup/app/tests/e2e && npm install && npx playwright install chromium
 *   REGRESSION_SUITE=1 PLAYWRIGHT_BASE_URL=http://127.0.0.1:7861 npx playwright test regression_live.spec.ts --workers=1
 *
 * HTTPS (self-signed):
 *   REGRESSION_SUITE=1 PLAYWRIGHT_BASE_URL=https://127.0.0.1:7860 npx playwright test regression_live.spec.ts --workers=1
 *
 * Covers (HTTP + browser smoke): discovery pages, RAG upload/ingest/files, attachments,
 * instructions API, face runtime, text-chat proxy, vision preview, local-tools, voice test pages,
 * and delegates WebSocket vision+VLM validation to the Python harness (optional).
 */
import { test, expect } from "@playwright/test";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { isLiveBaseConfigured, getLlmModelForChat, tinyJpegBytes } from "./live_helpers";

const appDir = path.resolve(process.cwd(), "..", "..");

test.describe.configure({ mode: "serial" });

test.beforeEach(({}, testInfo) => {
  if (!isLiveBaseConfigured()) {
    testInfo.skip(true, "Set PLAYWRIGHT_BASE_URL to the bot (e.g. http://127.0.0.1:7861)");
  }
});

test.describe("Discovery & static routes", () => {
  test("GET /api/runtime-status", async ({ request }) => {
    const r = await request.get("/api/runtime-status");
    expect(r.ok(), await r.text()).toBeTruthy();
    const j = await r.json();
    expect(j).toHaveProperty("models");
    expect(j).toHaveProperty("gpu");
  });

  test("GET /api/mobile-voice + /api/mobile-voice-vision", async ({ request }) => {
    for (const p of ["/api/mobile-voice", "/api/mobile-voice-vision"]) {
      const r = await request.get(p);
      expect(r.ok(), `${p} ${await r.text()}`).toBeTruthy();
      const j = await r.json();
      expect(typeof j).toBe("object");
    }
  });

  test("GET /api/local-tools", async ({ request }) => {
    const r = await request.get("/api/local-tools");
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    expect(typeof j.local_tools_enable_default).toBe("boolean");
    expect(j).toHaveProperty("rag");
    expect(j).toHaveProperty("calendar");
    expect(Array.isArray(j.tool_http_allow_hosts)).toBeTruthy();
  });

  test("Pages load: assistant-console, mobile voice, mobile voice+vision", async ({ page }) => {
    for (const u of ["/assistant-console", "/mobile-voice-test", "/mobile-voice-vision-test"]) {
      const resp = await page.goto(u, { waitUntil: "domcontentloaded", timeout: 45_000 });
      expect(resp?.ok() || resp?.status() === 0, `${u} status ${resp?.status()}`).toBeTruthy();
    }
  });

  test("Session manager + instructions + RAG arena + face manager (smoke load)", async ({ page }) => {
    for (const u of ["/session-manager", "/instructions-manager", "/rag-arena", "/face-manager"]) {
      const resp = await page.goto(u, { waitUntil: "domcontentloaded", timeout: 45_000 });
      expect(resp?.ok() || resp?.status() === 0, u).toBeTruthy();
    }
  });
});

test.describe("RAG pipeline", () => {
  const marker = `e2e_rag_marker_${Date.now()}`;
  let uploadedPath: string | null = null;

  test("GET /api/rag/status", async ({ request }) => {
    const r = await request.get("/api/rag/status");
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    expect(j).toHaveProperty("documents_dir");
  });

  test("POST /api/rag/upload (.txt) + ingest + /api/rag/files", async ({ request }) => {
    const body = `Hello RAG regression.\nUnique line: ${marker}\nEnd.\n`;
    const r = await request.post("/api/rag/upload", {
      multipart: {
        dest: "documents",
        file: {
          name: "regression_e2e.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(body, "utf-8"),
        },
      },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
    const up = await r.json();
    expect(up.ok).toBeTruthy();
    const saved = up.saved as { path?: string }[];
    expect(saved?.length).toBeGreaterThan(0);
    uploadedPath = saved[0]?.path || null;
    expect(uploadedPath).toBeTruthy();

    const ing = await request.post("/api/rag/ingest", {
      data: { reindex_all: true },
    });
    expect(ing.ok(), await ing.text()).toBeTruthy();

    const files = await request.get("/api/rag/files?limit=800");
    expect(files.ok()).toBeTruthy();
    const fj = await files.json();
    const paths = JSON.stringify(fj);
    expect(paths).toContain("regression_e2e.txt");
  });

  test("cleanup: remove uploaded RAG file", async ({ request }) => {
    test.skip(!uploadedPath, "no path from upload step");
    const r = await request.post("/api/rag/remove", {
      data: { path: uploadedPath },
    });
    expect([200, 400].includes(r.status())).toBeTruthy();
    const j = await r.json();
    expect(j.ok).toBeTruthy();
  });
});

test.describe("Chat attachments", () => {
  test("POST /api/chat/attachments (small text file)", async ({ request }) => {
    const sid = `e2e_attach_${Date.now()}`;
    const r = await request.post("/api/chat/attachments", {
      multipart: {
        session_id: sid,
        file: {
          name: "note.txt",
          mimeType: "text/plain",
          buffer: Buffer.from("attachment e2e content", "utf-8"),
        },
      },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
    const j = await r.json();
    expect(j.session_id || j.items).toBeTruthy();
  });
});

test.describe("Instructions API", () => {
  const id = `e2e_instr_${Date.now()}`;

  test("GET /api/instructions", async ({ request }) => {
    const r = await request.get("/api/instructions");
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    expect(j.items).toBeDefined();
    expect(Array.isArray(j.items)).toBeTruthy();
  });

  test("POST + GET + DELETE instruction", async ({ request }) => {
    const create = await request.post("/api/instructions", {
      data: {
        id,
        title: "E2E regression instruction",
        prompt: "You are a test stub. Reply: OK-e2e",
        overwrite: true,
      },
    });
    expect(create.ok(), await create.text()).toBeTruthy();

    const g = await request.get(`/api/instructions/${id}`);
    expect(g.ok()).toBeTruthy();
    const doc = await g.json();
    expect(doc.prompt).toContain("OK-e2e");

    const del = await request.delete(`/api/instructions/${id}`);
    expect(del.ok()).toBeTruthy();
  });
});

test.describe("Face runtime API", () => {
  test("GET /api/face/runtime", async ({ request }) => {
    const r = await request.get("/api/face/runtime");
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    for (const k of ["env_enabled", "runtime_enabled", "effective_enabled"] as const) {
      expect(typeof j[k]).toBe("boolean");
    }
  });
});

test.describe("Text chat (LLM path)", () => {
  test("POST /api/text-chat/completions — short non-stream", async ({ request }) => {
    const model = await getLlmModelForChat(request);
    const r = await request.post("/api/text-chat/completions", {
      headers: { "Content-Type": "application/json" },
      data: {
        model,
        stream: false,
        max_tokens: 24,
        temperature: 0,
        messages: [
          { role: "system", content: "You are concise." },
          { role: "user", content: "Reply with exactly: pong-e2e" },
        ],
      },
      timeout: 240_000,
    });
    if (!r.ok()) {
      const t = await r.text();
      expect.soft(r.status(), `LLM chat failed: ${t}`).toBeLessThan(600);
      test.skip(r.status() >= 502, `Upstream LLM unavailable (${r.status()}): ${t.slice(0, 400)}`);
    }
    const j = await r.json();
    const content = (j?.choices?.[0]?.message?.content || "").toString().toLowerCase();
    expect(content.length).toBeGreaterThan(0);
  });

  test("POST /api/text-chat/completions — local tools discovery (agent path)", async ({ request }) => {
    const model = await getLlmModelForChat(request);
    const r = await request.post("/api/text-chat/completions", {
      headers: { "Content-Type": "application/json" },
      data: {
        model,
        stream: false,
        max_tokens: 64,
        temperature: 0,
        local_tools_enable: true,
        tool_rag: true,
        tool_calendar: false,
        tool_connector: false,
        messages: [
          { role: "system", content: "Use tools if needed. Be brief." },
          { role: "user", content: "Search the local knowledge base for word: regression" },
        ],
      },
      timeout: 300_000,
    });
    if (!r.ok()) {
      const t = await r.text();
      test.skip(r.status() >= 502, `LLM/agent unavailable: ${t.slice(0, 300)}`);
    }
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    expect(j?.choices?.[0]?.message?.content || j?.choices?.[0]?.message?.tool_calls).toBeTruthy();
  });
});

test.describe("Vision preview (Tier-A VLM)", () => {
  test("POST /api/vision/preview (multipart JPEG)", async ({ request }) => {
    const buf = tinyJpegBytes();
    const r = await request.post("/api/vision/preview", {
      multipart: {
        image: {
          name: "probe.jpg",
          mimeType: "image/jpeg",
          buffer: buf,
        },
      },
      timeout: 180_000,
    });
    if (!r.ok()) {
      const t = await r.text();
      test.skip(r.status() >= 502, `Vision/Ollama unavailable: ${t.slice(0, 400)}`);
    }
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    expect(j.ok).toBeTruthy();
    expect(String(j.caption || "").length).toBeGreaterThan(1);
  });
});

test.describe("Assistant Console UI (smoke)", () => {
  test("Lisa console: critical controls present", async ({ page }) => {
    await page.goto("/assistant-console", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page.locator(".title")).toContainText(/Lisa Assistant/i, { timeout: 20_000 });
    await expect(page.getByRole("button", { name: /Voice/i }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Session Manager/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Instructions Manager/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /RAG Manager/i })).toBeVisible();
  });
});

test.describe("Python harness: WebSocket vision + text-chat (full vision path)", () => {
  test("run scripts/e2e_vision_voice_test.py", async () => {
    const base = process.env.PLAYWRIGHT_BASE_URL!.replace(/\/$/, "");
    const script = path.join(appDir, "scripts", "e2e_vision_voice_test.py");
    const args = ["run", "python", script, "--base-url", base];
    if (base.startsWith("https")) args.push("--insecure");
    const res = spawnSync("uv", args, {
      cwd: appDir,
      encoding: "utf-8",
      timeout: 420_000,
      shell: false,
    });
    if (res.error) {
      test.skip(true, `uv not available: ${String(res.error)}`);
    }
    expect(res.status, res.stderr || res.stdout).toBe(0);
  });
});
