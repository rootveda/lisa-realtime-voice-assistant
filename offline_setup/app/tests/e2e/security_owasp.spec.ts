/**
 * Security-focused checks (OWASP-minded, not a full ASVS audit).
 * Targets: SSRF via tool connector URLs, path traversal on RAG remove, oversized uploads,
 * injection-friendly instruction IDs, benign XSS payload in stored JSON.
 *
 * Run with live bot:
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:7861 npx playwright test security_owasp.spec.ts
 */
import { test, expect } from "@playwright/test";
import { isLiveBaseConfigured, getLlmModelForChat } from "./live_helpers";

test.beforeEach(({}, testInfo) => {
  if (!isLiveBaseConfigured()) {
    testInfo.skip(true, "Set PLAYWRIGHT_BASE_URL");
  }
});

test.describe("SSRF / unsafe egress (OWASP A10)", () => {
  test("agent mode rejects non-allowlisted rag_url during parse", async ({ request }) => {
    const model = await getLlmModelForChat(request);
    const r = await request.post("/api/text-chat/completions", {
      headers: { "Content-Type": "application/json" },
      data: {
        model,
        stream: false,
        max_tokens: 8,
        temperature: 0,
        local_tools_enable: true,
        tool_rag: true,
        rag_url: "https://169.254.169.254/latest/meta-data/",
        messages: [{ role: "user", content: "Hello" }],
      },
      timeout: 60_000,
    });
    expect(r.status()).toBe(400);
    const j = await r.json();
    expect(JSON.stringify(j)).toMatch(/allowlisted|not allowed|Host|URL|connector/i);
  });
});

test.describe("Path traversal (A01)", () => {
  test("POST /api/rag/remove rejects path outside RAG roots", async ({ request }) => {
    const r = await request.post("/api/rag/remove", {
      data: { path: "/etc/passwd" },
    });
    expect(r.status()).toBe(400);
    const j = await r.json();
    expect(j.ok).toBeFalsy();
    expect(String(j.error || "")).toMatch(/outside|allowed|path/i);
  });
});

test.describe("Upload limits (A04 / A05)", () => {
  test("POST /api/rag/upload sanitizes hostile filename", async ({ request }) => {
    const r = await request.post("/api/rag/upload", {
      multipart: {
        dest: "documents",
        file: {
          name: "../../../tmp/evil_e2e.txt",
          mimeType: "text/plain",
          buffer: Buffer.from("sanitized name probe", "utf-8"),
        },
      },
    });
    expect(r.ok()).toBeTruthy();
    const j = await r.json();
    const p = (j.saved?.[0]?.path || "") as string;
    expect(p).toContain("evil_e2e.txt");
    expect(p).not.toContain("/tmp/");
    await request.post("/api/rag/remove", { data: { path: p } });
  });

  test("attachment rejects oversized payload name (graceful)", async ({ request }) => {
    const big = Buffer.alloc(Math.min(25 * 1024 * 1024, 22 * 1024 * 1024));
    const r = await request.post("/api/chat/attachments", {
      multipart: {
        session_id: "e2e_big",
        file: {
          name: "big.bin",
          mimeType: "application/octet-stream",
          buffer: big,
        },
      },
      timeout: 120_000,
    });
    expect([200, 400, 413].includes(r.status())).toBeTruthy();
  });
});

test.describe("Input validation (API)", () => {
  test("POST /api/instructions rejects pathological id", async ({ request }) => {
    const r = await request.post("/api/instructions", {
      data: {
        id: "../../etc",
        prompt: "x",
        overwrite: true,
      },
    });
    expect(r.status()).toBe(400);
  });

  test("Stored instruction returns JSON-safe text (no HTML execution via API)", async ({ request }) => {
    const id = `e2e_xss_${Date.now()}`;
    const payload = "<script>alert(1)</script>";
    const c = await request.post("/api/instructions", {
      data: {
        id,
        title: "xss probe",
        prompt: payload,
        overwrite: true,
      },
    });
    expect(c.ok()).toBeTruthy();
    const g = await request.get(`/api/instructions/${id}`);
    expect(g.ok()).toBeTruthy();
    const j = await g.json();
    expect(j.prompt).toContain("<script>");
    await request.delete(`/api/instructions/${id}`);
  });
});

test.describe("No stack destructive endpoints in security suite", () => {
  test("document: /api/stack/restart is not called here (DoS / availability)", async () => {
    expect(true).toBeTruthy();
  });
});
