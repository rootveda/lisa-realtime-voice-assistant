"""Multi-step chat/completions with local tools (RAG, calendar, allowlisted HTTP)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from pipecat_bots import local_calendar, local_rag
from pipecat_bots.local_tool_executor import http_request_allowed, validate_user_connector_url


@dataclass
class ToolRuntimeContext:
    tool_rag: bool = False
    tool_calendar: bool = False
    tool_connector: bool = False
    rag_url: str | None = None
    calendar_url: str | None = None
    connector_url: str | None = None


def _tool_definitions(ctx: ToolRuntimeContext) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if ctx.tool_rag:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "description": "Search the local offline document index (markdown/text ingested under rag_data/documents). Use for facts from your manuals and notes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Keywords or question for lexical search"},
                        },
                        "required": ["query"],
                    },
                },
            }
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "read_indexed_file_excerpt",
                    "description": "Read a text excerpt from an indexed file by filename/path fragment (for 'explain file X' requests).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Filename/path fragment, e.g. toy.txt"},
                            "max_chars": {"type": "integer", "description": "Optional excerpt size cap (default 4000)"},
                        },
                        "required": ["query"],
                    },
                },
            }
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "search_indexed_files",
                    "description": "Find indexed files by filename/path (metadata lookup when users mention file names).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Filename or path fragment, e.g. toy.txt"},
                        },
                        "required": ["query"],
                    },
                },
            }
        )
    if ctx.tool_calendar:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "list_calendar_events",
                    "description": "List upcoming and recent events from the local calendar database.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_ts": {
                                "type": "integer",
                                "description": "Unix epoch start of window (optional; default last 7 days)",
                            },
                            "end_ts": {
                                "type": "integer",
                                "description": "Unix epoch end of window (optional; default +90 days)",
                            },
                        },
                    },
                },
            }
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "create_calendar_event",
                    "description": "Add an event to the local calendar.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "start_ts": {"type": "integer", "description": "Unix epoch seconds"},
                            "end_ts": {"type": "integer", "description": "Optional end epoch"},
                            "notes": {"type": "string"},
                        },
                        "required": ["title", "start_ts"],
                    },
                },
            }
        )
    if ctx.tool_rag and ctx.rag_url:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "query_rag_connector",
                    "description": "Call the user-configured local RAG HTTP endpoint (same-origin or loopback only).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                        },
                        "required": ["query"],
                    },
                },
            }
        )
    if ctx.tool_calendar and ctx.calendar_url:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "query_calendar_connector",
                    "description": "POST to the user-configured local calendar HTTP service.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "payload": {
                                "type": "object",
                                "description": "JSON body forwarded to the connector",
                            },
                        },
                        "required": ["payload"],
                    },
                },
            }
        )
    if ctx.tool_connector and ctx.connector_url:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "query_generic_connector",
                    "description": "POST JSON to the configured allowlisted local connector URL (offline automation).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "payload": {"type": "object"},
                        },
                        "required": ["payload"],
                    },
                },
            }
        )
    return tools


async def _run_tool(name: str, raw_args: str | None, ctx: ToolRuntimeContext) -> str:
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        args = {}
    try:
        if name == "search_knowledge_base":
            q = str(args.get("query", "")).strip()
            out = local_rag.search_knowledge_base(q)
            return json.dumps(out, ensure_ascii=False)[:24000]
        if name == "search_indexed_files":
            q = str(args.get("query", "")).strip()
            out = local_rag.search_indexed_files(q)
            return json.dumps(out, ensure_ascii=False)[:24000]
        if name == "read_indexed_file_excerpt":
            q = str(args.get("query", "")).strip()
            mc = args.get("max_chars")
            out = local_rag.read_indexed_file_excerpt(q, max_chars=int(mc) if mc is not None else 4000)
            return json.dumps(out, ensure_ascii=False)[:24000]
        if name == "list_calendar_events":
            st = args.get("start_ts")
            et = args.get("end_ts")
            out = local_calendar.list_events(
                start_ts=int(st) if st is not None else None,
                end_ts=int(et) if et is not None else None,
            )
            return json.dumps(out, ensure_ascii=False)[:24000]
        if name == "create_calendar_event":
            out = local_calendar.add_event(
                str(args.get("title", "")),
                int(args.get("start_ts", 0)),
                end_ts=int(args["end_ts"]) if args.get("end_ts") is not None else None,
                notes=str(args.get("notes") or ""),
            )
            return json.dumps(out, ensure_ascii=False)
        if name == "query_rag_connector" and ctx.rag_url:
            r = await http_request_allowed(
                "POST",
                ctx.rag_url,
                json_body={"query": args.get("query", "")},
                timeout=45.0,
                context="rag_connector",
            )
            txt = r.text[:24000]
            return json.dumps({"status": r.status_code, "body": txt})
        if name == "query_calendar_connector" and ctx.calendar_url:
            body = args.get("payload") if isinstance(args.get("payload"), dict) else {}
            r = await http_request_allowed(
                "POST",
                ctx.calendar_url,
                json_body=body,
                timeout=45.0,
                context="calendar_connector",
            )
            return json.dumps({"status": r.status_code, "body": r.text[:24000]})
        if name == "query_generic_connector" and ctx.connector_url:
            body = args.get("payload") if isinstance(args.get("payload"), dict) else {}
            r = await http_request_allowed(
                "POST",
                ctx.connector_url,
                json_body=body,
                timeout=45.0,
                context="generic_connector",
            )
            return json.dumps({"status": r.status_code, "body": r.text[:24000]})
    except Exception as e:
        logger.warning(f"[text_chat_agent] tool {name} error: {e}")
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps({"ok": False, "error": "unknown tool or disabled"})


def _parse_tool_runtime(payload: dict[str, Any]) -> tuple[dict[str, Any], ToolRuntimeContext | None]:
    """Strip extension fields; return cleaned payload and context if agent mode."""
    if not payload.get("local_tools_enable"):
        return payload, None
    ctx = ToolRuntimeContext(
        tool_rag=bool(payload.pop("tool_rag", False)),
        tool_calendar=bool(payload.pop("tool_calendar", False)),
        tool_connector=bool(payload.pop("tool_connector", False)),
    )
    rag_u = payload.pop("rag_url", None)
    cal_u = payload.pop("calendar_url", None)
    con_u = payload.pop("connector_url", None)
    try:
        ctx.rag_url = validate_user_connector_url(str(rag_u) if rag_u else None, label="rag_url")
        ctx.calendar_url = validate_user_connector_url(str(cal_u) if cal_u else None, label="calendar_url")
        ctx.connector_url = validate_user_connector_url(str(con_u) if con_u else None, label="connector_url")
    except ValueError as e:
        raise ValueError(str(e)) from e
    payload.pop("local_tools_enable", None)
    return payload, ctx


MAX_AGENT_ROUNDS = int(os.environ.get("LOCAL_AGENT_MAX_ROUNDS", "8"))


def _prefetch_rag_hits_into_messages(messages: list[dict[str, Any]], ctx: ToolRuntimeContext) -> None:
    """Add a short system context block from local RAG before first model call.

    This helps when the model declines tool-calls for meta questions like
    "Do you have access to my files?" even though indexed documents exist.
    """
    if not ctx.tool_rag:
        return
    if os.environ.get("LOCAL_AGENT_RAG_PREFETCH", "1").strip().lower() in ("0", "false", "no"):
        return
    # Always prime capability semantics for meta questions ("do you have access to my files?")
    try:
        st = local_rag.rag_status()
        fd = local_rag.rag_files_detail(limit=5)
        names = [x.get("name") for x in (fd.get("files") or []) if x.get("name")]
        names_txt = ", ".join(names[:5]) if names else "no indexed filenames available"
        cap = (
            "[Local RAG capability]\n"
            "In this chat, local tools are enabled. You can access indexed local files by using the "
            "search_knowledge_base tool. Do not claim zero access when asked about files; clarify that "
            "you can access only indexed files (not the whole computer).\n"
            f"Indexed files count: {st.get('files_on_disk', 0)}; chunked: {st.get('files_chunked', 0)}.\n"
            f"Example indexed filenames: {names_txt}."
        )
        messages.insert(0, {"role": "system", "content": cap[:3000]})
    except Exception:
        pass

    last_user = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            last_user = m["content"].strip()
            break
    if not last_user:
        return
    try:
        lim = int(os.environ.get("LOCAL_AGENT_RAG_PREFETCH_HITS", "5"))
    except ValueError:
        lim = 5
    lim = max(1, min(lim, 12))
    out = local_rag.search_knowledge_base(last_user, limit=lim)
    hits = out.get("hits") if isinstance(out, dict) else None
    if not hits:
        # Fallback for file-name prompts where body-snippet search can miss.
        fmeta = local_rag.search_indexed_files(last_user, limit=8)
        fhits = fmeta.get("hits") if isinstance(fmeta, dict) else None
        if fhits:
            names = "\n".join(f"- {x.get('path')}" for x in fhits[:8])
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "[Indexed file-name matches]\n"
                        "The following indexed files match the user query:\n"
                        f"{names}"
                    )[:6000],
                },
            )
        return
    lines = []
    for h in hits[:lim]:
        p = h.get("path", "")
        s = (h.get("snippet") or "").replace("\n", " ")
        lines.append(f"- ({p}) {s}")
    preface = (
        "[Offline RAG prefetch]\n"
        "The following snippets were retrieved from indexed local files. "
        "You DO have access to these indexed files via local tools in this chat.\n"
        + "\n".join(lines)
    )
    messages.insert(0, {"role": "system", "content": preface[:6000]})


async def run_agent_chat_completions(
    payload: dict[str, Any],
    *,
    upstream_post,
    truncate_max_tokens: int | None = None,
    truncate_max_chars: int | None = None,
) -> tuple[bytes, int, str]:
    """Run multi-step tool loop; upstream_post is async (body: bytes) -> httpx.Response with .content .status_code."""
    import copy

    try:
        base_payload, ctx = _parse_tool_runtime(copy.deepcopy(payload))
    except ValueError as e:
        err = json.dumps({"error": str(e)}).encode()
        return err, 400, "application/json"
    if ctx is None:
        raise ValueError("agent mode not enabled")

    tools = _tool_definitions(ctx)
    if not tools:
        from pipecat_bots.text_chat_truncate import truncate_messages_for_upstream

        if isinstance(base_payload.get("messages"), list):
            base_payload["messages"], _ = truncate_messages_for_upstream(
                base_payload["messages"],
                max_prompt_tokens=truncate_max_tokens,
                max_chars=truncate_max_chars,
            )
        body = json.dumps(base_payload).encode()
        resp = await upstream_post(body)
        return (
            resp.content,
            resp.status_code,
            resp.headers.get("content-type") or "application/json",
        )

    base_payload["tools"] = tools
    if "tool_choice" not in base_payload:
        base_payload["tool_choice"] = "auto"

    messages = base_payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages required")
    _prefetch_rag_hits_into_messages(messages, ctx)
    # Prefetch inserts large system rows — must trim after (proxy truncate runs before prefetch).
    from pipecat_bots.text_chat_truncate import truncate_messages_for_upstream

    base_payload["messages"], _pref_trim = truncate_messages_for_upstream(
        messages,
        max_prompt_tokens=truncate_max_tokens,
        max_chars=truncate_max_chars,
    )
    messages = base_payload["messages"]

    last_status = 502
    last_ct = "application/json"

    for _round in range(MAX_AGENT_ROUNDS):
        base_payload["messages"], _ = truncate_messages_for_upstream(
            base_payload["messages"],
            max_prompt_tokens=truncate_max_tokens,
            max_chars=truncate_max_chars,
        )
        body = json.dumps(base_payload).encode()
        resp = await upstream_post(body)
        last_status = resp.status_code
        last_ct = resp.headers.get("content-type") or "application/json"
        raw = resp.content
        if resp.status_code != 200:
            return raw, last_status, last_ct

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw, last_status, last_ct

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            return raw, last_status, last_ct

        messages.append(
            {
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            }
        )

        for tc in tool_calls:
            tid = tc.get("id") or ""
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            arguments = fn.get("arguments") or ""
            result = await _run_tool(name, arguments, ctx)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": result,
                }
            )

        base_payload["messages"] = messages
        # Drop tool_choice auto after first round so model can finish
        base_payload.pop("tool_choice", None)

    logger.warning("[text_chat_agent] max agent rounds exceeded")
    err = json.dumps(
        {"error": "local agent exceeded max tool rounds", "max_rounds": MAX_AGENT_ROUNDS}
    ).encode()
    return err, 500, "application/json"
