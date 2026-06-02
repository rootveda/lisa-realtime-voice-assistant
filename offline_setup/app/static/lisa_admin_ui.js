(function (global) {
  "use strict";

  const TOKEN_KEY = "assistantConsole.lisaAdminToken.v1";
  const STYLE_ID = "lisa-admin-ui-style";
  const OVERLAY_ID = "lisaNoticeOverlay";
  const BANNER_ID = "securityModeBanner";
  const SIDE_PANEL_SELECTORS = [
    "[data-lisa-security-panel]",
    ".panel.mission-panel .controls",
    ".grid > .panel:first-child",
    ".grid > .card:first-child",
    "#detailPane",
    "#ragDetailPane",
    "aside.detail-pane",
  ];

  let adminTokenRequired = false;
  let tokenInputId = null;
  let tokenWrapId = null;
  let onCapabilities = null;
  let autoBootEnabled = true;

  function safeTrim(v) {
    return String(v ?? "").trim();
  }

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement("style");
    s.id = STYLE_ID;
    s.textContent = `
      .lisa-notice-overlay {
        position: fixed;
        inset: 0;
        background: rgba(3, 8, 18, 0.85);
        backdrop-filter: blur(2px);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 10000;
      }
      .lisa-notice-overlay.show { display: flex; }
      .lisa-notice-card {
        border: 1px solid #243357;
        background: #0b1426;
        border-radius: 12px;
        padding: 16px;
        width: min(520px, 92vw);
        color: #dff6ff;
        box-shadow: 0 0 24px rgba(69, 230, 255, 0.15);
      }
      .lisa-notice-title {
        color: #45e6ff;
        font-weight: 700;
        margin-bottom: 8px;
      }
      .lisa-notice-title.error { color: #ff9e9e; }
      .lisa-notice-msg {
        color: #8cb8c7;
        font-size: 13px;
        line-height: 1.45;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .lisa-notice-actions {
        margin-top: 14px;
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      .lisa-notice-actions button {
        padding: 8px 12px;
        border-radius: 8px;
        border: 1px solid #355a8a;
        background: #0f1c34;
        color: #45e6ff;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        cursor: pointer;
      }
      .lisa-notice-actions button:hover { border-color: #45e6ff; }
      .lisa-notice-actions .primary-error {
        border-color: #7d3f3f;
        background: #2a1313;
        color: #ff9e9e;
      }
      .lisa-notice-actions .primary-error:hover { border-color: #ff9e9e; }
      .security-mode-banner {
        font-size: 12px;
        line-height: 1.45;
        padding: 8px 12px;
        border-radius: 8px;
        border: 1px solid #243357;
        background: #0d162b;
        color: #8cb8c7;
        margin-top: 12px;
        margin-bottom: 0;
      }
      .security-mode-banner.admin {
        border-color: #8b5432;
        background: #241810;
        color: #ffc9a0;
      }
      .security-mode-banner.home-dev {
        border-color: #2a6b4a;
        background: #0f1f18;
        color: #a8e6c3;
      }
      .security-mode-banner.fail-closed {
        border-color: #8b3232;
        background: #241010;
        color: #ffb0b0;
      }
      .security-mode-banner strong { font-weight: 700; }
      .security-mode-banner code {
        font-family: ui-monospace, monospace;
        font-size: 11px;
      }
      .security-mode-banner a {
        color: inherit;
        font-weight: 600;
        text-decoration: underline;
      }
      .lisa-side-panel {
        border: 1px solid #243357;
        border-radius: 10px;
        background: #0b1426;
        padding: 12px;
        align-self: start;
      }
      .lisa-page-layout {
        display: grid;
        grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
        gap: 16px;
        align-items: start;
      }
      @media (max-width: 768px) {
        .lisa-page-layout { grid-template-columns: 1fr; }
      }
    `;
    document.head.appendChild(s);
  }

  function isAssistantConsolePage() {
    try {
      const p = String(global.location?.pathname || "");
      return p === "/assistant-console" || p.endsWith("/assistant-console");
    } catch (_) {
      return false;
    }
  }

  function hasLocalTokenField() {
    if (tokenInputId && document.getElementById(tokenInputId)) return true;
    return !!(
      document.getElementById("lisaAdminTokenInput") ||
      document.getElementById("arenaAdminTokenInput")
    );
  }

  function tokenFieldLabel() {
    if (document.getElementById("arenaAdminTokenInput")) return "Admin token";
    return "LLM Routing";
  }

  function adminModeBannerHtml() {
    const base =
      "<strong>Admin mode</strong> — Bearer token required for stack changes " +
      "(Apply Routing, full restart, RAG ingest). Paste <code>LISA_ADMIN_TOKEN</code> in ";
    if (isAssistantConsolePage() || hasLocalTokenField()) {
      return base + "<strong>" + tokenFieldLabel() + "</strong> below.";
    }
    return (
      base +
      "<strong>LLM Routing</strong> on the " +
      '<a href="/assistant-console">Assistant Console</a>.'
    );
  }

  function findSidePanelHost() {
    for (let i = 0; i < SIDE_PANEL_SELECTORS.length; i++) {
      const el = document.querySelector(SIDE_PANEL_SELECTORS[i]);
      if (el) return el;
    }
    return null;
  }

  function ensureSecurityBannerEl() {
    injectStyles();
    let el = document.getElementById(BANNER_ID);
    if (el) return el;
    const host = findSidePanelHost();
    if (!host) return null;
    el = document.createElement("div");
    el.id = BANNER_ID;
    el.className = "security-mode-banner";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.textContent = "Checking security mode…";
    host.appendChild(el);
    return el;
  }

  function applySecurityBannerState(el, j) {
    if (!el) return;
    if (!j || typeof j !== "object") {
      el.classList.remove("admin", "home-dev", "fail-closed");
      el.textContent = "Security mode unknown (could not load /api/admin/capabilities).";
      return;
    }
    const adminRequired = !!j.admin_token_required;
    const tokenConfigured = !!j.token_configured;
    const loopbackOnly = !!j.bind_loopback_only;
    el.classList.remove("admin", "home-dev", "fail-closed");
    if (adminRequired) {
      el.classList.add("admin");
      el.innerHTML = adminModeBannerHtml();
      return;
    }
    if (!loopbackOnly && !tokenConfigured) {
      el.classList.add("fail-closed");
      el.innerHTML =
        "<strong>Admin gate fail-closed</strong> — server has <code>LISA_BIND_LOOPBACK_ONLY=0</code> but no token. " +
        "Set <code>LISA_ADMIN_TOKEN</code> and restart the stack.";
      return;
    }
    el.classList.add("home-dev");
    el.innerHTML =
      "<strong>Home dev mode</strong> — loopback-only bind; admin actions from this machine work without a token. " +
      "LAN clients cannot reach the stack unless you enable public bind + admin token.";
  }

  function renderSecurityBanner(j) {
    if (global.self !== global.top) return;
    applySecurityBannerState(ensureSecurityBannerEl(), j);
  }

  function autoBoot() {
    if (!autoBootEnabled || global.self !== global.top) return;
    refreshCapabilities().catch(function () {});
  }

  function injectOverlay() {
    if (document.getElementById(OVERLAY_ID)) return;
    injectStyles();
    const el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.className = "lisa-notice-overlay";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.innerHTML =
      '<div class="lisa-notice-card">' +
      '<div id="lisaNoticeTitle" class="lisa-notice-title">Notice</div>' +
      '<div id="lisaNoticeMsg" class="lisa-notice-msg"></div>' +
      '<div class="lisa-notice-actions">' +
      '<button id="lisaNoticeOkBtn" type="button">OK</button>' +
      "</div></div>";
    document.body.appendChild(el);
  }

  function normalizeAdminToken(raw) {
    let s = safeTrim(raw);
    if (!s) return "";
    s = s.replace(/^export\s+LISA_ADMIN_TOKEN\s*=\s*/i, "");
    s = s.replace(/^Bearer\s+/i, "");
    s = s.replace(/[\r\n]+/g, "");
    s = safeTrim(s.replace(/^['"]|['"]$/g, ""));
    return s;
  }

  function allTokenInputs() {
    const els = [];
    if (tokenInputId) {
      const primary = document.getElementById(tokenInputId);
      if (primary) els.push(primary);
    }
    document.querySelectorAll("[data-lisa-admin-token]").forEach(function (el) {
      if (els.indexOf(el) < 0) els.push(el);
    });
    return els;
  }

  function tokenInputEl() {
    const toolbar = document.querySelector("[data-lisa-admin-token-focus]");
    if (toolbar) return toolbar;
    const inputs = allTokenInputs();
    return inputs.length ? inputs[0] : null;
  }

  function syncTokenInputs(tok) {
    const normalized = normalizeAdminToken(tok);
    if (!normalized) return;
    allTokenInputs().forEach(function (el) {
      el.value = normalized;
    });
  }

  function tokenValue() {
    for (let i = 0; i < allTokenInputs().length; i++) {
      const tok = normalizeAdminToken(allTokenInputs()[i].value || "");
      if (tok) return tok;
    }
    try {
      return normalizeAdminToken(global.localStorage.getItem(TOKEN_KEY) || "");
    } catch (_) {
      return "";
    }
  }

  function persistToken() {
    let tok = "";
    for (let i = 0; i < allTokenInputs().length; i++) {
      tok = normalizeAdminToken(allTokenInputs()[i].value || "");
      if (tok) break;
    }
    if (!tok) return;
    try {
      global.localStorage.setItem(TOKEN_KEY, tok);
    } catch (_) {}
    syncTokenInputs(tok);
  }

  function showTokenWraps(visible) {
    const display = visible ? "block" : "none";
    if (tokenWrapId) {
      const wrap = document.getElementById(tokenWrapId);
      if (wrap) wrap.style.display = display;
    }
    document.querySelectorAll("[data-lisa-admin-token-wrap]").forEach(function (wrap) {
      wrap.style.display = display;
    });
  }

  function initTokenField() {
    let saved = "";
    try {
      saved = normalizeAdminToken(global.localStorage.getItem(TOKEN_KEY) || "");
    } catch (_) {}
    allTokenInputs().forEach(function (el) {
      if (saved && !normalizeAdminToken(el.value || "")) el.value = saved;
      el.addEventListener("change", persistToken);
      el.addEventListener("blur", persistToken);
      el.addEventListener("input", persistToken);
    });
  }

  function adminHeaders(extra) {
    const h = Object.assign({}, extra || {});
    const tok = tokenValue();
    if (tok) {
      h.Authorization = "Bearer " + tok;
      h["X-Lisa-Admin-Token"] = tok;
    }
    return h;
  }

  function focusTokenField() {
    showTokenWraps(true);
    const el =
      document.querySelector("[data-lisa-admin-token-focus]") ||
      document.getElementById("arenaAdminTokenInputToolbar") ||
      tokenInputEl();
    if (el) {
      try {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
      } catch (_) {}
      el.focus();
      try {
        el.select();
      } catch (_) {}
    }
  }

  async function ensureAdminToken(actionLabel) {
    if (!adminTokenRequired) return true;
    const tok = tokenValue();
    if (tok) return true;
    const label = safeTrim(actionLabel || "This action");
    await showAuthError(
      `${label} requires the admin token.\n\n` +
        "Paste the token only (from offline_setup/lisa_admin_token.env) in the Admin token field above the Delete buttons, then try again.",
      label
    );
    focusTokenField();
    return false;
  }

  async function refreshCapabilities() {
    try {
      const r = await fetch("/api/admin/capabilities", { cache: "no-store" });
      if (!r.ok) {
        if (tokenWrapId || document.querySelector("[data-lisa-admin-token]")) {
          adminTokenRequired = true;
          showTokenWraps(true);
        }
        return null;
      }
      const j = await r.json();
      adminTokenRequired = !!j.admin_token_required;
      showTokenWraps(adminTokenRequired);
      renderSecurityBanner(j);
      if (typeof onCapabilities === "function") onCapabilities(j);
      return j;
    } catch (_) {
      if (tokenWrapId || document.querySelector("[data-lisa-admin-token]")) {
        adminTokenRequired = true;
        showTokenWraps(true);
      }
      return null;
    }
  }

  function showNotice(message, opts) {
    injectOverlay();
    const options = opts || {};
    const title = options.title || "Notice";
    const okText = options.okText || "OK";
    const isError = !!options.error;
    const overlay = document.getElementById(OVERLAY_ID);
    const titleEl = document.getElementById("lisaNoticeTitle");
    const msgEl = document.getElementById("lisaNoticeMsg");
    const okBtn = document.getElementById("lisaNoticeOkBtn");
    titleEl.textContent = title;
    titleEl.classList.toggle("error", isError);
    msgEl.textContent = String(message || "");
    okBtn.textContent = okText;
    okBtn.classList.toggle("primary-error", isError);
    overlay.classList.add("show");

    return new Promise(function (resolve) {
      function done() {
        overlay.classList.remove("show");
        okBtn.classList.remove("primary-error");
        okBtn.removeEventListener("click", onOk);
        overlay.removeEventListener("click", onBackdrop);
        document.removeEventListener("keydown", onKey);
        resolve();
      }
      function onOk() {
        done();
      }
      function onBackdrop(e) {
        if (e.target === overlay) done();
      }
      function onKey(e) {
        if (e.key === "Escape" || e.key === "Enter") done();
      }
      okBtn.addEventListener("click", onOk);
      overlay.addEventListener("click", onBackdrop);
      document.addEventListener("keydown", onKey);
      okBtn.focus();
    });
  }

  function authHint() {
    if (adminTokenRequired) {
      if (document.querySelector("[data-lisa-admin-token-focus]")) {
        return (
          "Paste LISA_ADMIN_TOKEN in the Admin token field above the Delete buttons " +
          "(token only — not the whole export line). Shared with Assistant Console → LLM Routing."
        );
      }
      return (
        "Paste LISA_ADMIN_TOKEN in the Admin token field on this page " +
        "(or Assistant Console → LLM Routing) and try again."
      );
    }
    return "Connect from this machine (127.0.0.1) or set LISA_ADMIN_TOKEN on the server.";
  }

  function authDeniedTitle(actionLabel) {
    if (!actionLabel) return "Not authorized";
    const label = String(actionLabel).trim();
    const lower = label.toLowerCase();
    if (lower.startsWith("delete ") || lower.startsWith("upload ") || lower.startsWith("run ")) {
      return `Unable to ${lower}`;
    }
    return `${label} not allowed`;
  }

  function showAuthError(message, actionLabel) {
    const title = authDeniedTitle(actionLabel);
    const body = safeTrim(message || "Unauthorized.") + "\n\n" + authHint();
    const el = tokenInputEl();
    if (el) focusTokenField();
    return showNotice(body, { title: title, okText: "OK", error: true });
  }

  async function checkAuthResponse(res, data, actionLabel) {
    if (!res || (res.status !== 401 && res.status !== 403)) return false;
    const msg =
      (data && (data.error || data.message)) ||
      (res.status === 401 ? "Unauthorized." : "Forbidden.");
    await showAuthError(msg, actionLabel);
    return true;
  }

  function isAuthMessage(message) {
    const m = safeTrim(message).toLowerCase();
    return (
      m.includes("authorization") ||
      m.includes("admin token") ||
      m.includes("unauthorized") ||
      m.includes("forbidden") ||
      m.includes("not authorized")
    );
  }

  async function notifyError(message, actionLabel) {
    if (isAuthMessage(message)) {
      await showAuthError(message, actionLabel);
      return;
    }
    await showNotice(safeTrim(message || "An error occurred."), {
      title: actionLabel ? actionLabel + " failed" : "Error",
      error: true,
    });
  }

  function init(opts) {
    opts = opts || {};
    tokenInputId = opts.tokenInputId || null;
    tokenWrapId = opts.tokenWrapId || null;
    onCapabilities = opts.onCapabilities || null;
    if (opts.autoBoot === false) autoBootEnabled = false;
    initTokenField();
    return refreshCapabilities();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoBoot);
  } else {
    autoBoot();
  }

  global.LisaAdminUI = {
    TOKEN_KEY: TOKEN_KEY,
    init: init,
    tokenValue: tokenValue,
    persistToken: persistToken,
    adminHeaders: adminHeaders,
    refreshCapabilities: refreshCapabilities,
    renderSecurityBanner: renderSecurityBanner,
    adminTokenRequired: function () {
      return adminTokenRequired;
    },
    ensureAdminToken: ensureAdminToken,
    focusTokenField: focusTokenField,
    showNotice: showNotice,
    showAuthError: showAuthError,
    checkAuthResponse: checkAuthResponse,
    notifyError: notifyError,
    isAuthMessage: isAuthMessage,
  };
})(window);
