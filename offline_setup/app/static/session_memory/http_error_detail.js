/**
 * Non-2xx fetch: read JSON {error,message} or text body (Ollama + proxy).
 * Loaded by sci_fi_assistant.html and Playwright harness — keep one implementation.
 */
(function (global) {
  "use strict";

  async function fetchHttpErrorDetail(res) {
    const status = res.status;
    try {
      const ct = (res.headers.get("content-type") || "").toLowerCase();
      if (ct.includes("application/json")) {
        const j = await res.json();
        const msg =
          (typeof j.error === "string" && j.error) ||
          (typeof j.message === "string" && j.message) ||
          (j.error && typeof j.error === "object" && JSON.stringify(j.error)) ||
          "";
        if (msg) return "HTTP " + status + " — " + String(msg).slice(0, 800);
      } else {
        const t = await res.text();
        if (t) return "HTTP " + status + " — " + t.slice(0, 500);
      }
    } catch (_) {}
    return "HTTP " + status;
  }

  global.fetchHttpErrorDetail = fetchHttpErrorDetail;
})(typeof window !== "undefined" ? window : globalThis);
