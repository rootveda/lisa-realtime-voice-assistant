(() => {
  const SESSIONS_KEY = "assistantConsole.sessions.v1";
  const ACTIVE_KEY = "assistantConsole.activeSession.v1";
  const SERVER_SYNC_DEBOUNCE_MS = 500;

  let _serverSyncTimer = null;
  let _serverSyncInFlight = false;

  const nowTs = () => Date.now();
  const mkId = () => `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const normalizeEpochMs = (v) => {
    const n = Number(v) || 0;
    if (!n) return nowTs();
    return n < 1e12 ? Math.floor(n * 1000) : Math.floor(n);
  };
  const normalizeMessages = (arr) =>
    Array.isArray(arr)
      ? arr.map((m) => ({
          ...m,
          ts: m && m.ts != null ? normalizeEpochMs(m.ts) : undefined,
        }))
      : [];
  const normalizeSession = (s) => ({
    id: s?.id || mkId(),
    name: (s?.name || "Untitled session").trim(),
    createdAt: normalizeEpochMs(s?.createdAt),
    updatedAt: normalizeEpochMs(s?.updatedAt),
    messages: normalizeMessages(s?.messages),
    settings: { ragEnabled: s?.settings?.ragEnabled !== false },
  });

  function writeSessionsLocal(arr) {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(arr));
  }

  function loadSessions() {
    try {
      const raw = localStorage.getItem(SESSIONS_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(arr)) return [];
      const normalized = arr.map(normalizeSession);
      if (JSON.stringify(arr) !== JSON.stringify(normalized)) writeSessionsLocal(normalized);
      return normalized;
    } catch (_) {
      return [];
    }
  }

  function saveSessions(arr) {
    writeSessionsLocal(arr);
    scheduleServerSync();
  }

  function latestLocalUpdatedAt(sessions) {
    return (sessions || []).reduce((m, s) => Math.max(m, Number(s.updatedAt) || 0), 0);
  }

  function scheduleServerSync() {
    if (_serverSyncTimer) clearTimeout(_serverSyncTimer);
    _serverSyncTimer = setTimeout(() => {
      pushToServer().catch(() => {});
    }, SERVER_SYNC_DEBOUNCE_MS);
  }

  async function pushToServer() {
    if (_serverSyncInFlight) return false;
    _serverSyncInFlight = true;
    try {
      const sessions = loadSessions();
      const updatedAt = Math.max(latestLocalUpdatedAt(sessions), nowTs());
      const payload = {
        version: 1,
        activeId: getActiveId(),
        sessions,
        updatedAt,
      };
      const r = await fetch("/api/assistant-console/chat-sessions", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return r.ok;
    } catch (_) {
      return false;
    } finally {
      _serverSyncInFlight = false;
    }
  }

  async function syncFromServer() {
    const local = loadSessions();
    const localUpdated = latestLocalUpdatedAt(local);
    let server = null;
    try {
      const r = await fetch("/api/assistant-console/chat-sessions", { cache: "no-store" });
      if (!r.ok) return false;
      server = await r.json();
    } catch (_) {
      return false;
    }
    const serverSessions = Array.isArray(server?.sessions)
      ? server.sessions.map(normalizeSession)
      : [];
    const serverUpdated = Number(server?.updatedAt) || latestLocalUpdatedAt(serverSessions);

    if (!serverSessions.length && local.length) {
      await pushToServer();
      return true;
    }
    if (serverSessions.length && localUpdated > serverUpdated) {
      await pushToServer();
      return true;
    }
    if (serverSessions.length) {
      writeSessionsLocal(serverSessions);
      const active = String(server?.activeId || "").trim();
      const nextActive =
        active && serverSessions.some((s) => s.id === active)
          ? active
          : serverSessions[0]?.id || "";
      localStorage.setItem(ACTIVE_KEY, nextActive);
      return true;
    }
    return false;
  }

  function getActiveId() {
    return localStorage.getItem(ACTIVE_KEY) || "";
  }

  function setActiveId(id) {
    localStorage.setItem(ACTIVE_KEY, id || "");
    scheduleServerSync();
  }

  function listSummaries() {
    return loadSessions()
      .map((s) => ({
        id: s.id,
        name: s.name || "Untitled session",
        createdAt: s.createdAt || 0,
        updatedAt: s.updatedAt || 0,
        messageCount: Array.isArray(s.messages) ? s.messages.length : 0,
        ragEnabled: s.settings?.ragEnabled !== false,
      }))
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  }

  function getSession(id) {
    return loadSessions().find((s) => s.id === id) || null;
  }

  function createSession(name = "New session") {
    const arr = loadSessions();
    const s = normalizeSession({
      id: mkId(),
      name,
      messages: [],
      settings: { ragEnabled: true },
    });
    arr.push(s);
    saveSessions(arr);
    setActiveId(s.id);
    return s;
  }

  function upsertSession(session) {
    const arr = loadSessions();
    const i = arr.findIndex((s) => s.id === session.id);
    const normalized = normalizeSession({ ...session, updatedAt: nowTs() });
    if (i >= 0) arr[i] = { ...arr[i], ...normalized, settings: normalized.settings };
    else arr.push(normalized);
    saveSessions(arr);
    return normalized;
  }

  function renameSession(id, name) {
    const arr = loadSessions();
    const i = arr.findIndex((s) => s.id === id);
    if (i < 0) return null;
    arr[i].name = (name || "").trim() || arr[i].name || "Untitled session";
    arr[i].updatedAt = nowTs();
    saveSessions(arr);
    return arr[i];
  }

  function deleteSession(id) {
    const arr = loadSessions().filter((s) => s.id !== id);
    saveSessions(arr);
    if (getActiveId() === id) {
      setActiveId(arr[0]?.id || "");
    }
    return arr.length;
  }

  function clearSessionMessages(id) {
    const arr = loadSessions();
    const i = arr.findIndex((s) => s.id === id);
    if (i < 0) return null;
    arr[i].messages = [];
    arr[i].updatedAt = nowTs();
    saveSessions(arr);
    return arr[i];
  }

  function clearAllMessages() {
    const arr = loadSessions();
    let changed = 0;
    const now = nowTs();
    for (const s of arr) {
      if (Array.isArray(s.messages) && s.messages.length) {
        s.messages = [];
        s.updatedAt = now;
        changed += 1;
      }
    }
    if (changed) saveSessions(arr);
    return { changed, total: arr.length };
  }

  function ensureActiveSession() {
    const active = getActiveId();
    if (active) {
      const s = getSession(active);
      if (s) return s;
    }
    const list = listSummaries();
    if (list.length) {
      setActiveId(list[0].id);
      return getSession(list[0].id);
    }
    return createSession("Session 1");
  }

  function dedupeByName() {
    const arr = loadSessions();
    if (!arr.length) return { removed: 0, keptActiveId: getActiveId() || "" };
    const activeId = getActiveId();
    const groups = new Map();
    for (const s of arr) {
      const key = (s.name || "Untitled session").trim().toLowerCase();
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    }
    let removed = 0;
    const next = [];
    let nextActiveId = activeId;
    for (const group of groups.values()) {
      if (group.length === 1) {
        next.push(group[0]);
        continue;
      }
      const preferred =
        group.find((s) => s.id === activeId) ||
        group.slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))[0];
      const mergedMessages = [];
      for (const s of group) {
        for (const m of Array.isArray(s.messages) ? s.messages : []) {
          if (m && m.role && m.content) mergedMessages.push(m);
        }
      }
      const dedupedMessages = [];
      const seen = new Set();
      for (const m of mergedMessages) {
        const ts = Number(m.ts) || 0;
        const key = `${m.role}::${m.content}::${ts}`;
        if (seen.has(key)) continue;
        seen.add(key);
        dedupedMessages.push(m);
      }
      dedupedMessages.sort((a, b) => (Number(a.ts) || 0) - (Number(b.ts) || 0));
      const merged = {
        ...preferred,
        createdAt: Math.min(...group.map((s) => Number(s.createdAt) || nowTs())),
        updatedAt: Math.max(...group.map((s) => Number(s.updatedAt) || 0)),
        messages: dedupedMessages,
      };
      next.push(normalizeSession(merged));
      removed += group.length - 1;
      if (group.some((s) => s.id === activeId)) nextActiveId = preferred.id;
    }
    if (!removed) return { removed: 0, keptActiveId: activeId || "" };
    saveSessions(next);
    if (nextActiveId) setActiveId(nextActiveId);
    return { removed, keptActiveId: nextActiveId || "" };
  }

  window.AssistantSessionStore = {
    listSummaries,
    getSession,
    createSession,
    upsertSession,
    renameSession,
    deleteSession,
    clearSessionMessages,
    clearAllMessages,
    getActiveId,
    setActiveId,
    ensureActiveSession,
    dedupeByName,
    syncFromServer,
    pushToServer,
  };
})();
