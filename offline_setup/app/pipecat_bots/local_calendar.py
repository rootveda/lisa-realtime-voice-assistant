"""Local-only calendar (SQLite) — no Google / cloud."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from pipecat_bots.repo_root import resolve_repo_root


def _cal_dir() -> Path:
    root = resolve_repo_root()
    p = root / "offline_setup" / "rag_data"
    custom = (os.environ.get("LOCAL_CALENDAR_DB_PATH") or "").strip()
    if custom:
        return Path(custom).expanduser().resolve().parent
    p.mkdir(parents=True, exist_ok=True)
    return p


def calendar_db_path() -> Path:
    custom = (os.environ.get("LOCAL_CALENDAR_DB_PATH") or "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return _cal_dir() / "calendar.sqlite"


def _connect() -> sqlite3.Connection:
    calendar_db_path().parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(calendar_db_path()))
    con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


def init_schema() -> None:
    con = _connect()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS local_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_ts INTEGER NOT NULL,
                end_ts INTEGER,
                notes TEXT
            );
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_local_events_start ON local_events(start_ts);")
        con.commit()
    finally:
        con.close()


def list_events(*, start_ts: int | None = None, end_ts: int | None = None, limit: int = 50) -> dict[str, Any]:
    init_schema()
    con = _connect()
    now = int(time.time())
    st = start_ts if start_ts is not None else now - 86400 * 7
    et = end_ts if end_ts is not None else now + 86400 * 90
    try:
        cur = con.execute(
            """
            SELECT id, title, start_ts, end_ts, notes
            FROM local_events
            WHERE start_ts >= ? AND start_ts <= ?
            ORDER BY start_ts ASC
            LIMIT ?
            """,
            (st, et, max(1, min(limit, 200))),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        con.close()
    return {"ok": True, "events": rows}


def add_event(
    title: str,
    start_ts: int,
    *,
    end_ts: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    init_schema()
    t = (title or "").strip()
    if not t:
        return {"ok": False, "error": "title required"}
    con = _connect()
    try:
        con.execute(
            "INSERT INTO local_events(title, start_ts, end_ts, notes) VALUES (?,?,?,?)",
            (t, int(start_ts), end_ts, (notes or "")[:4000]),
        )
        con.commit()
        eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        con.close()
    return {"ok": True, "id": eid}


def calendar_status() -> dict[str, Any]:
    init_schema()
    con = _connect()
    try:
        n = con.execute("SELECT count(*) FROM local_events").fetchone()[0]
    finally:
        con.close()
    return {"db_path": str(calendar_db_path()), "events": n}
