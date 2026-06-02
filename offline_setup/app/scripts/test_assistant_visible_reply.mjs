/**
 * Mirrors sci_fi_assistant.html assistant-visible logic — run:
 *   node test_assistant_visible_reply.mjs
 */
function safeTrim(value) {
  return (typeof value === "string" ? value : "").trim();
}

function stripThinkingPreamble(raw) {
  let s = String(raw || "").trim();
  s = s.replace(/<\|[^>|]+\|>/g, "");
  if (!/Thinking Process:/i.test(s)) return s;
  const paras = s.split(/\n\n+/);
  const tail = paras.length > 1 ? paras[paras.length - 1].trim() : "";
  if (tail && !/^Thinking Process:/i.test(tail) && tail.length > 0 && tail.length < s.length) return tail;
  const kept = paras.filter((p) => {
    const t = p.trim();
    if (/^Thinking Process:/i.test(t)) return false;
    if (/^Here's a thinking process/i.test(t)) return false;
    return true;
  });
  const out = kept.join("\n\n").trim();
  return out.length >= 8 ? out : s;
}

function stripNumberedAnalysisSteps(raw) {
  let s = String(raw || "").trim();
  let guard = 0;
  while (guard++ < 48 && /^\d+\.\s+\*\*/.test(s)) {
    const next = s.replace(/^\d+\.\s+\*\*[^*]+\*\*[^\n]*\n?/, "").trim();
    if (next === s) break;
    s = next;
  }
  return s;
}

function sanitizeDisplayText(text) {
  let t = stripThinkingPreamble(String(text || ""));
  t = stripNumberedAnalysisSteps(t);
  t = t.replace(/<\|?\/?channel\|?>/gi, "");
  t = t.replace(/^\s*thought\b[\s:,\-]*/i, "");
  t = t.replace(/''+/g, "'");
  t = t.replace(/\s{2,}/g, " ");
  return t.trim();
}

function looksLikeThinkingTrace(s) {
  const t = safeTrim(s);
  if (!t) return true;
  if (/Thinking Process:/i.test(t)) return true;
  if (/Here's a thinking process/i.test(t)) return true;
  if (/^\d+\.\s+\*\*Analyze the Request/i.test(t)) return true;
  if (/^\d+\.\s+\*\*[^*]+\*\*:/.test(t) && t.length > 500) return true;
  return false;
}

function looksLikeAssistantMetaInstruction(s) {
  const t = safeTrim(s);
  if (!t || t.length > 280) return false;
  if (/self-?correction|\/refinement/i.test(t)) return true;
  if (/^\*[^*\n]{0,120}\*\s*[\/:]/i.test(t)) return true;
  if (/^(acknowledge|respond|provide|state|confirm|clarify|summarize|analyze|determine|consider|refine)\b/i.test(t))
    return true;
  return false;
}

function fallbackAnswerFromBlob(raw) {
  const s = safeTrim(raw);
  if (!s) return "";
  const lines = s.split("\n").map((l) => l.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    const ln = lines[i];
    if (/^Thinking Process:/i.test(ln)) continue;
    if (/^\d+\.\s+\*\*[^*]+\*\*/.test(ln)) continue;
    if (/^\d+\.\s+\*\*/.test(ln) && ln.length > 240) continue;
    if (ln.length < 2 || ln.length > 900) continue;
    const c = sanitizeDisplayText(ln);
    if (c && !looksLikeThinkingTrace(c)) return c;
  }
  return "";
}

function firstDraftBulletFromReasoning(raw) {
  const s = safeTrim(raw);
  if (!s) return "";
  const hasDraftSection =
    /\bDraft Options\b/i.test(s) ||
    /\bInternal Monologue\b/i.test(s) ||
    /\bInternal Monologue:/i.test(s);
  const lines = s.split(/\n/);
  for (const line of lines) {
    const m = line.match(/^(\s*)[*•]\s+(.+)$/);
    if (!m) continue;
    const indent = m[1].length;
    const rest = m[2].trim();
    const trimmedLine = line.trim();
    const looseBullet = /^\*\s{2,}\S/.test(trimmedLine);
    const indentedBullet = indent >= 2;
    if (!hasDraftSection && !looseBullet && !indentedBullet) continue;

    let cand = rest.replace(/\s*\([^)]*\)\s*$/, "").trim();
    cand = sanitizeDisplayText(cand);
    if (!cand || looksLikeThinkingTrace(cand)) continue;
    if (cand.length < 1 || cand.length > 600) continue;
    return cand;
  }
  return "";
}

function assistantVisibleReply(msg) {
  const rawC = typeof msg.content === "string" ? msg.content : "";
  const rawR = typeof msg.reasoning === "string" ? msg.reasoning : "";
  const reasoningPresent = safeTrim(rawR).length > 0;

  const fromC = sanitizeDisplayText(rawC);
  if (fromC && !looksLikeThinkingTrace(fromC)) {
    if (!reasoningPresent || !looksLikeAssistantMetaInstruction(fromC)) return fromC;
  }

  const fromR = sanitizeDisplayText(rawR);
  const draftPick = firstDraftBulletFromReasoning(rawR);
  const reasoningHasMonologueSection =
    /\bDraft Options\b/i.test(rawR) ||
    /\bInternal Monologue\b/i.test(rawR) ||
    /\bInternal Monologue:/i.test(rawR);

  if (fromR && !looksLikeThinkingTrace(fromR)) {
    const multiLine = fromR.split("\n").filter((l) => l.trim()).length > 1;
    const leadingBullet = /^\s*\*\s/.test(fromR);
    if (draftPick && (reasoningHasMonologueSection || multiLine || leadingBullet)) return draftPick;
    return fromR;
  }

  if (draftPick && reasoningHasMonologueSection) return draftPick;

  const fb =
    fallbackAnswerFromBlob(rawC) ||
    fallbackAnswerFromBlob(rawR) ||
    fallbackAnswerFromBlob(`${rawC}\n${rawR}`);
  if (fb) return fb;

  if (draftPick) return draftPick;

  return "";
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assert failed");
}

// --- regressions & prod-shaped payloads ---
assert(sanitizeDisplayText("Hello") === "Hello", "plain passthrough");

const gemmaMetaOnly = `1. **Analyze the Request:** The user input is simply "hi".
2. **Analyze the Context/Instructions:** The provided context is a set of system instructions.
3. **Determine the Goal:** The user is initiating a casual`;
assert(sanitizeDisplayText(gemmaMetaOnly) === "", "strip Gemma 1. **Analyze:** steps entirely");

assert(
  sanitizeDisplayText(
    '1. **Analyze:** quick check.\n\nHey! Good to see you.'
  ).includes("Hey!"),
  "strip steps but keep real reply paragraph"
);

assert(
  looksLikeThinkingTrace("Thinking Process: 1. foo"),
  "detect thinking trace header"
);
assert(!looksLikeThinkingTrace("I am Gemma 4."), "short answer not trace");

// BUG: empty content, reasoning starts with Thinking Process but ends with answer — must still extract
const reasoningWithTail =
  "Thinking Process:\n1. **Identify:** User asks name.\n2. **Answer:** Use identity.\n\nI am Gemma 4, developed by Google DeepMind.";
assert(
  assistantVisibleReply({ content: "", reasoning: reasoningWithTail }).includes("Gemma 4"),
  "REGRESSION: empty content + reasoning with tail answer must not be empty"
);

// Never return raw trace as first choice when content is trace-only blob (sanitized still trace → skip to reasoning / fallback)
assert(
  assistantVisibleReply({
    content: "Thinking Process:\n1. **Foo**\n2. **Bar**",
    reasoning: "I am Gemma 4.",
  }).includes("Gemma 4"),
  "trace-only content yields answer from reasoning"
);

assert(
  assistantVisibleReply({ content: "", reasoning: "Thinking Process: long trace..." }) === "",
  "reasoning that stays trace-only after sanitize stays hidden"
);

assert(
  assistantVisibleReply({ content: "", reasoning: "Canberra." }) === "Canberra.",
  "short reasoning-only answer"
);

assert(
  assistantVisibleReply({ content: "Short reply.", reasoning: "Thinking Process: ..." }) === "Short reply.",
  "prefer clean content over reasoning"
);

assert(
  assistantVisibleReply({
    content: "Acknowledge the greeting warmly.",
    reasoning: "Hey! What's up?",
  }) === "Hey! What's up?",
  "ignore meta content when non-empty reasoning carries the reply"
);

assert(
  assistantVisibleReply({ content: "Acknowledge the greeting warmly.", reasoning: "" }) === "Acknowledge the greeting warmly.",
  "meta-looking content kept when no reasoning field"
);

assert(
  looksLikeAssistantMetaInstruction("*Self-Correction/Refinement:*"),
  "detect self-correction label line"
);

// Single-line tail via fallback
assert(
  fallbackAnswerFromBlob("Thinking Process: x\n\ny\n\nMy name is Lisa.") === "My name is Lisa.",
  "fallback picks last clean line"
);

// Gemma/Ollama: empty content, reasoning = Thinking Process + Draft Options bullets only
const gemmaDraftOnly = `Thinking Process:
1.  **Analyze the Request:** The user said "hi".
4.  **Draft Options (Internal Monologue):**
    *   Hi there. (Good, short, casual)
    *   Hey! What's up? (Good, casual)`;
assert(
  assistantVisibleReply({ content: "", reasoning: gemmaDraftOnly }) === "Hi there.",
  "extract first draft bullet when content empty and reasoning is trace+drafts"
);

assert(
  assistantVisibleReply({
    content: "Hello from content.",
    reasoning: gemmaDraftOnly,
  }) === "Hello from content.",
  "draft extraction never overrides non-empty content"
);

console.log("test_assistant_visible_reply.mjs: all checks passed.");
