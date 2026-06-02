"""SQLite registry for enrolled persons (embedding blob optional)."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from loguru import logger

from pipecat_bots.face_recog.config import registry_db_path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
  person_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  embedding BLOB,
  notes TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id TEXT NOT NULL,
  vision_session_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY(person_id) REFERENCES persons(person_id)
);

CREATE INDEX IF NOT EXISTS idx_turns_person ON conversation_turns(person_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(vision_session_id);
"""


def _connect() -> sqlite3.Connection:
    path = registry_db_path()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        logger.info(f"[face_recog] registry db={registry_db_path()}")
    return _conn


def upsert_person(
    person_id: str,
    display_name: str,
    embedding: bytes | None = None,
    notes: str | None = None,
) -> None:
    pid = person_id.strip()
    if not pid:
        return
    now = time.time()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO persons (person_id, display_name, embedding, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(person_id) DO UPDATE SET
          display_name = excluded.display_name,
          embedding = COALESCE(excluded.embedding, persons.embedding),
          notes = COALESCE(excluded.notes, persons.notes),
          updated_at = excluded.updated_at
        """,
        (pid, display_name.strip() or pid, embedding, notes, now, now),
    )
    conn.commit()


def get_person(person_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM persons WHERE person_id = ?", (person_id.strip(),)).fetchone()
    return dict(row) if row else None


def _is_placeholder_display(name: str) -> bool:
    n = (name or "").strip().lower()
    return not n or n in ("unknown", "—", "-", "n/a", "none", "unnamed")


def identity_is_enrolled(person_id: str) -> bool:
    """True when registry row looks like a human-chosen display name (not Unknown / id fallback).

    ``upsert_person`` stores ``display_name = person_id`` when the caller passes an empty name;
    that must not count as enrollment.
    """
    pid = (person_id or "").strip()
    if not pid:
        return False
    row = get_person(pid)
    if not row:
        return False
    dn = str(row.get("display_name") or "").strip()
    if _is_placeholder_display(dn):
        return False
    if dn == pid:
        return False
    return True


def list_persons(limit: int = 500) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT person_id, display_name, notes, created_at FROM persons ORDER BY display_name LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def append_turn(person_id: str, role: str, content: str, vision_session_id: str | None = None) -> None:
    pid = person_id.strip()
    if not pid:
        return
    # Main UI face stream can be read-only for registry writes; avoid FK failures
    # when session-state IDs are not persisted in persons table.
    if get_person(pid) is None:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO conversation_turns (person_id, vision_session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, (vision_session_id or "").strip() or None, role, content, time.time()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Race-safe guard: if person row disappears between check and insert, skip.
        return


def recent_turns_for_person(person_id: str, limit: int = 8) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT role, content, created_at, vision_session_id FROM conversation_turns
        WHERE person_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (person_id.strip(), limit),
    ).fetchall()
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def delete_person(person_id: str) -> None:
    pid = person_id.strip()
    conn = get_connection()
    conn.execute("DELETE FROM conversation_turns WHERE person_id = ?", (pid,))
    conn.execute("DELETE FROM persons WHERE person_id = ?", (pid,))
    conn.commit()
    try:
        from pipecat_bots.face_recog.face_thumbnails import remove_for_person

        remove_for_person(pid)
    except Exception:
        pass
