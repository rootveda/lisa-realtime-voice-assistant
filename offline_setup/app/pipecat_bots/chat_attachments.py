"""Session-scoped attachment files for text chat (offline extraction)."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import hashlib
from pathlib import Path

from loguru import logger

from pipecat_bots.repo_root import resolve_repo_root

from pipecat_bots.local_rag import (
    RAG_IMAGE_SUFFIXES,
    RAG_INDEXABLE_SUFFIXES,
    RAG_MEDIA_SUFFIXES,
    _extract_text_from_path,
    describe_image_from_bytes,
    describe_media_binary_from_bytes,
)


_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_DEFAULT_PDF_PAGE_CAP = 80
_DEFAULT_TEXT_MAX_CHARS = 120_000


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        v = int(raw)
        if v < minimum:
            return default
        return v
    except (TypeError, ValueError):
        logger.warning("[chat_attachments] invalid {}={!r}; using default {}", name, raw, default)
        return default


def chat_attach_max_bytes() -> int:
    return _env_int("CHAT_ATTACH_MAX_BYTES", _DEFAULT_MAX_BYTES, minimum=1024)


def chat_attach_pdf_page_cap() -> int:
    return _env_int("CHAT_ATTACH_PDF_PAGE_CAP", _DEFAULT_PDF_PAGE_CAP, minimum=1)


def chat_attach_text_max_chars() -> int:
    return _env_int("CHAT_ATTACH_TEXT_MAX_CHARS", _DEFAULT_TEXT_MAX_CHARS, minimum=1024)


def _attach_root() -> Path:
    root = resolve_repo_root()
    d = root / "offline_setup" / "rag_data" / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_dir(session_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "", (session_id or "default"))[:80]
    if not safe:
        safe = "default"
    p = _attach_root() / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def _try_pdf_text(data: bytes) -> str | None:
    try:
        from io import BytesIO

        from pypdf import PdfReader

        page_cap = chat_attach_pdf_page_cap()
        text_cap = chat_attach_text_max_chars()
        r = PdfReader(BytesIO(data))
        parts = []
        for page in r.pages[:page_cap]:
            t = page.extract_text() or ""
            if t:
                parts.append(t)
        out = "\n\n".join(parts).strip()
        return out[:text_cap] if out else None
    except Exception as e:
        logger.debug(f"[chat_attachments] PDF extract: {e}")
        return None


def save_uploads_sync(
    session_id: str,
    files: list[tuple[str, bytes, str]],
) -> dict:
    """Synchronous core of :func:`save_uploads`; safe to call from a worker thread."""
    sid = session_id.strip() or secrets.token_hex(8)
    out_dir = _session_dir(sid)
    manifest: dict = {"session_id": sid, "items": []}
    max_bytes = chat_attach_max_bytes()
    text_cap = chat_attach_text_max_chars()

    for fname, raw, ctype in files:
        if not isinstance(raw, (bytes, bytearray)):
            manifest["items"].append({"name": fname, "error": "invalid binary payload"})
            continue
        if len(raw) > max_bytes:
            manifest["items"].append({"name": fname, "error": f"file too large (>{max_bytes} bytes)"})
            continue
        aid = secrets.token_hex(12)
        ext = Path(fname).suffix.lower() or ".bin"
        fpath = out_dir / f"{aid}{ext}"
        try:
            fpath.write_bytes(raw)
        except OSError as e:
            logger.warning("[chat_attachments] disk write failed: {}", e)
            manifest["items"].append({"name": fname, "error": f"disk write failed: {e}"})
            continue
        low = (fname or "").lower()
        suf = Path(fname).suffix.lower()
        text_out = None
        if low.endswith((".txt", ".md", ".markdown", ".csv", ".json", ".log")):
            try:
                text_out = raw.decode("utf-8", errors="replace")[:text_cap]
            except Exception:
                text_out = None
        elif low.endswith(".pdf"):
            text_out = _try_pdf_text(raw)
        elif suf in RAG_IMAGE_SUFFIXES:
            text_out = describe_image_from_bytes(raw, fname)
        elif suf in RAG_MEDIA_SUFFIXES:
            text_out = describe_media_binary_from_bytes(fname, raw, ctype or "")
        if not text_out and suf in RAG_INDEXABLE_SUFFIXES:
            t2, err = _extract_text_from_path(fpath)
            if not err and t2 is not None:
                text_out = t2[:text_cap]
        rec = {
            "id": aid,
            "name": fname,
            "path": str(fpath),
            "bytes": len(raw),
            "mime": ctype or "application/octet-stream",
            "extracted_text": text_out or "",
        }
        try:
            (out_dir / f"{aid}.meta.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.warning("[chat_attachments] meta write failed: {}", e)
        manifest["items"].append({"id": aid, "name": fname, "bytes": len(raw), "has_text": bool(text_out)})
        try:
            _mirror_attachment_for_rag(sid, aid, fname, raw, text_out)
        except Exception as e:
            logger.warning(f"[chat_attachments] RAG mirror skipped: {e}")

    manifest["saved_at"] = int(time.time())
    return manifest


async def save_uploads(
    session_id: str,
    files: list[tuple[str, bytes, str]],
) -> dict:
    """Async wrapper that runs the blocking save path off the event loop."""
    import asyncio

    return await asyncio.to_thread(save_uploads_sync, session_id, files)


def _mirror_attachment_for_rag(
    session_id: str,
    attachment_id: str,
    fname: str,
    raw: bytes,
    extracted_text: str | None,
) -> None:
    """Keep a copy under rag_data/from_chat/ and ingest searchable text into FTS."""
    from pipecat_bots.local_rag import ingest_file, rag_from_chat_dir, write_dedup_linked_file

    safe_sid = re.sub(r"[^a-zA-Z0-9._-]", "", (session_id or "default"))[:64]
    if not safe_sid:
        safe_sid = "default"
    dest_root = rag_from_chat_dir() / safe_sid
    dest_root.mkdir(parents=True, exist_ok=True)
    base_name = Path(fname).name
    safe_base = re.sub(r"[^\w.\-]", "_", base_name)[:120] or "file"
    content_key = hashlib.sha256(raw).hexdigest()[:24]
    mirror_path = dest_root / f"{content_key}_{safe_base}"
    ext = Path(fname).suffix.lower() or ".bin"
    write_dedup_linked_file(mirror_path, raw, suffix=ext)

    paths_to_index: list[Path] = []
    suf_mirror = Path(fname).suffix.lower()
    txt_blob = (extracted_text or "").strip()
    if suf_mirror in RAG_INDEXABLE_SUFFIXES:
        paths_to_index.append(mirror_path)
    elif txt_blob:
        tx_key = hashlib.sha256(txt_blob[:120_000].encode("utf-8")).hexdigest()[:24]
        tx_path = dest_root / f"{tx_key}_{safe_base}_extracted.txt"
        blob = txt_blob[:120_000].encode("utf-8")
        write_dedup_linked_file(tx_path, blob, suffix=".txt")
        paths_to_index.append(tx_path)

    for p in paths_to_index:
        ingest_file(p)


_NO_EXTRACT_MSG = (
    "[No extractable text from this file (empty PDF, scanned/image-only pages, "
    "encrypted PDF, or optional dependency pypdf missing). Ask the user to paste "
    "an excerpt or convert to text.]"
)


def load_attachment_texts(session_id: str, ids: list[str]) -> str:
    """Load extracted text from ``<id>.meta.json`` files.

    Emits at least one line per requested id when possible so the model sees that an
    attachment was sent even when extraction yields nothing (common for scanned PDFs).
    """
    if not ids:
        return ""
    base = _session_dir(session_id)
    parts = []
    for aid in ids:
        aid_clean = re.sub(r"[^a-fA-F0-9]", "", str(aid))[:32]
        if not aid_clean:
            parts.append(f"--- Attachment (invalid id) ---\n{_NO_EXTRACT_MSG}")
            continue
        meta_path = base / f"{aid_clean}.meta.json"
        if not meta_path.is_file():
            parts.append(
                f"--- Attachment id {aid_clean} ---\n"
                "[No metadata on server — session mismatch, expired upload, or wrong attachment id.]"
            )
            continue
        try:
            rec = json.loads(meta_path.read_text(encoding="utf-8"))
            t = rec.get("extracted_text") or ""
            name = rec.get("name") or aid_clean
            if t.strip():
                parts.append(f"--- Attachment: {name} ---\n{t.strip()}")
            else:
                parts.append(f"--- Attachment: {name} ---\n{_NO_EXTRACT_MSG}")
        except (json.JSONDecodeError, OSError):
            parts.append(f"--- Attachment id {aid_clean} ---\n{_NO_EXTRACT_MSG}")
    return "\n\n".join(parts)[:150_000]


def augment_last_user_with_attachments(
    messages: list,
    session_id: str | None,
    attachment_ids: list[str] | None,
) -> list:
    """Append extracted attachment text to the last user message."""
    if not attachment_ids or not messages or not session_id:
        return messages
    blob = load_attachment_texts(session_id, attachment_ids)
    if not blob.strip():
        return messages
    out: list = []
    for m in messages:
        out.append(dict(m) if isinstance(m, dict) else m)
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            extra = f"\n\n[Attachments]\n{blob}"
            if isinstance(c, str):
                m["content"] = c + extra
            else:
                m["content"] = blob
            break
    return out
