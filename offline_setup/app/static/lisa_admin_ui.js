(function (global) {
  "use strict";

  const TOKEN_KEY = "assistantConsole.lisaAdminToken.v1";
  /** Real LISA_ADMIN_TOKEN values are long hex strings; ignore partial/autofill garbage. */
  const MIN_ADMIN_TOKEN_LEN = 16;
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
  let tokenStatusId = null;
  let onCapabilities = null;
  let autoBootEnabled = true;
  let tokenStatusTimer = null;

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
      .lisa-admin-token-status {
        margin: 0;
        font-size: 12px;
        font-weight: 600;
        line-height: 1.45;
        padding: 8px 10px;
        border-radius: 6px;
        border: 1px solid #243357;
        border-left-width: 4px;
        background: rgba(12, 18, 36, 0.55);
        color: #8cb8c7;
      }
      .lisa-admin-token-status.checking {
        color: #8cb8c7 !important;
        border-left-color: #45e6ff;
        background: rgba(12, 18, 36, 0.65);
      }
      .lisa-admin-token-status.ok {
        color: #7dffb2 !important;
        border-left-color: #3ecf7a;
        border-color: #2a6b4a;
        background: #0f2a1c;
      }
      .lisa-admin-token-status.warn {
        color: #ffb86a !important;
        border-left-color: #e8a040;
        border-color: #8b5432;
        background: #2a1c10;
      }
      .lisa-admin-token-status.bad {
        color: #ff8a8a !important;
        border-left-color: #e85c5c;
        border-color: #8b3232;
        background: #2a1010;
      }
      .lisa-admin-token-status.muted {
        color: #9eb8c8 !important;
        border-left-color: #4a6080;
        background: rgba(12, 18, 36, 0.45);
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
    return !!document.getElementById("lisaAdminTokenInput");
  }

  function unifiedTokenLocationHtml() {
    return (
      '<a href="/assistant-console">Assistant Console</a> → ' +
      "<strong>Workspace configuration → Admin token</strong>"
    );
  }

  function adminModeBannerHtml() {
    if (isAssistantConsolePage() || hasLocalTokenField()) {
      return (
        "<strong>Admin mode</strong> — Bearer token required for routing, full restart, RAG, and managers. " +
        "Paste it in the <strong>Admin token</strong> field below (from <code>offline_setup/lisa_admin_token.env</code>)."
      );
    }
    return (
      "<strong>Admin mode</strong> — Bearer token required for stack changes. " +
      "Paste it in " +
      unifiedTokenLocationHtml() +
      " (bottom of Workspace configuration)."
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
    if (s.length < MIN_ADMIN_TOKEN_LEN) return "";
    return s;
  }

  function clearStoredAdminToken() {
    try {
      global.localStorage.removeItem(TOKEN_KEY);
    } catch (_) {}
    allTokenInputs().forEach(function (el) {
      el.value = "";
    });
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
    const focus = document.querySelector("[data-lisa-admin-token-focus]");
    if (focus) return focus;
    const inputs = allTokenInputs();
    return inputs.length ? inputs[0] : null;
  }

  function syncTokenInputs(tok) {
    const normalized = normalizeAdminToken(tok);
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
    if (!tok) {
      clearStoredAdminToken();
      return;
    }
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

  function tokenStatusEl() {
    return tokenStatusId ? document.getElementById(tokenStatusId) : null;
  }

  function paintAdminTokenStatus(j) {
    const el = tokenStatusEl();
    if (!el || !j) return;
    const st = String(j.status || "");
    el.classList.remove("checking", "ok", "warn", "bad", "muted");
    if (st === "valid") el.classList.add("ok");
    else if (st === "invalid" || st === "error") el.classList.add("bad");
    else if (st === "missing") el.classList.add("warn");
    else if (st === "fail_closed") el.classList.add("bad");
    else el.classList.add("muted");
    el.textContent = String(j.message || "").trim() || "Status unknown.";
  }

  function scheduleAdminTokenStatusCheck() {
    if (tokenStatusTimer) clearTimeout(tokenStatusTimer);
    tokenStatusTimer = setTimeout(function () {
      tokenStatusTimer = null;
      refreshAdminTokenStatus().catch(function () {});
    }, 400);
  }

  async function refreshAdminTokenStatus() {
    const el = tokenStatusEl();
    if (!el) return null;
    if (!adminTokenRequired) {
      paintAdminTokenStatus({
        status: "not_required",
        message: "Home dev mode — admin token not required on this machine.",
      });
      return null;
    }
    const tok = tokenValue();
    if (!tok) {
      paintAdminTokenStatus({
        status: "missing",
        message: "Not entered — paste the token from lisa_admin_token.env.",
      });
      return null;
    }
    el.classList.remove("ok", "warn", "bad", "muted");
    el.classList.add("checking");
    el.textContent = "Checking — verifying token against server…";
    try {
      const r = await fetch("/api/admin/token-status", {
        cache: "no-store",
        headers: adminHeaders(),
      });
      const j = await r.json().catch(function () {
        return {};
      });
      if (!r.ok) {
        const msg =
          r.status === 404
            ? "Token check unavailable — restart the stack once (./offline_setup/lisa_stack.sh restart --admin)."
            : String(j.error || j.detail || j.message || `Token check failed (HTTP ${r.status}).`);
        paintAdminTokenStatus({ status: "error", message: msg });
        return j;
      }
      if (!j.status && !j.message) {
        paintAdminTokenStatus({
          status: "error",
          message: "Unexpected response from token check.",
        });
        return j;
      }
      paintAdminTokenStatus(j);
      return j;
    } catch (_) {
      paintAdminTokenStatus({
        status: "error",
        message: "Could not reach /api/admin/token-status.",
      });
      return null;
    }
  }

  function initTokenField() {
    let saved = "";
    try {
      const raw = global.localStorage.getItem(TOKEN_KEY) || "";
      saved = normalizeAdminToken(raw);
      if (raw && !saved) clearStoredAdminToken();
    } catch (_) {}
    allTokenInputs().forEach(function (el) {
      if (!normalizeAdminToken(el.value || "") && saved) el.value = saved;
      el.addEventListener("change", function () {
        persistToken();
        scheduleAdminTokenStatusCheck();
      });
      el.addEventListener("blur", function () {
        persistToken();
        scheduleAdminTokenStatusCheck();
      });
      el.addEventListener("input", function () {
        persistToken();
        scheduleAdminTokenStatusCheck();
      });
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
    if (!isAssistantConsolePage() && !hasLocalTokenField()) {
      return;
    }
    showTokenWraps(true);
    const el = tokenInputEl();
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
    const where =
      isAssistantConsolePage() || hasLocalTokenField()
        ? "Workspace configuration → Admin token at the bottom of the left panel."
        : "Assistant Console → Workspace configuration → Admin token.";
    await showAuthError(
      `${label} requires the admin token.\n\nPaste the token only (from offline_setup/lisa_admin_token.env) in ${where}`,
      label,
      { skipHint: true }
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
      if (tokenStatusEl()) {
        refreshAdminTokenStatus().catch(function () {
          scheduleAdminTokenStatusCheck();
        });
      }
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
      return (
        "Paste the admin token once in Assistant Console → Workspace configuration → Admin token " +
        "(token value only — not the export line). All manager pages use the same browser storage."
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

  function showAuthError(message, actionLabel, opts) {
    const title = authDeniedTitle(actionLabel);
    let body = safeTrim(message || "Unauthorized.");
    if (!(opts && opts.skipHint)) {
      body = body + "\n\n" + authHint();
    }
    focusTokenField();
    return showNotice(body, { title: title, okText: "OK", error: true });
  }

  async function checkAuthResponse(res, data, actionLabel) {
    if (!res || (res.status !== 401 && res.status !== 403)) return false;
    const msg =
      (data && (data.error || data.message)) ||
      (res.status === 401 ? "Unauthorized." : "Forbidden.");
    await showAuthError(msg, actionLabel);
    scheduleAdminTokenStatusCheck();
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
    tokenStatusId = opts.tokenStatusId || null;
    onCapabilities = opts.onCapabilities || null;
    if (opts.autoBoot === false) autoBootEnabled = false;
    initTokenField();
    return refreshCapabilities().then(function () {
      if (!tokenStatusEl()) return null;
      return refreshAdminTokenStatus();
    });
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
    refreshAdminTokenStatus: refreshAdminTokenStatus,
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
