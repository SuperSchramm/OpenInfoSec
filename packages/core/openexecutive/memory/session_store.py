from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openexecutive.memory.episodic import _get_conn, get_episodic_db_path

DB_PATH = get_episodic_db_path()


def _resolve_db_path(db_path: Path | None) -> Path:
    """Return the caller's path or the current module-level DB_PATH.

    Reading DB_PATH dynamically (not via default-arg binding) lets tests
    monkeypatch `openexecutive.memory.session_store.DB_PATH` and have it
    actually take effect — default arguments capture the value at def time.
    """
    return db_path if db_path is not None else DB_PATH


def create_session(
    session_id: str,
    title: str,
    created_at: str,
    caller_person_id: int | None = None,
    db_path: Path | None = None,
) -> None:
    with _get_conn(_resolve_db_path(db_path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, title, created_at, updated_at, caller_person_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, created_at, created_at, caller_person_id),
        )
        # Bind the owner late if turn 1 landed without a resolved caller
        # (e.g. principal not yet seeded, or DB lookup transiently failed)
        # and turn 2 succeeded in resolving one. INSERT OR IGNORE would
        # otherwise leave the row orphaned with caller_person_id = NULL,
        # invisible to its real owner forever.
        if caller_person_id is not None:
            conn.execute(
                "UPDATE sessions SET caller_person_id = ? "
                "WHERE session_id = ? AND caller_person_id IS NULL",
                (caller_person_id, session_id),
            )


def update_session_title(session_id: str, title: str, db_path: Path | None = None) -> None:
    with _get_conn(_resolve_db_path(db_path)) as conn:
        conn.execute("UPDATE sessions SET title = ? WHERE session_id = ?", (title, session_id))


def update_session_timestamp(session_id: str, db_path: Path | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    with _get_conn(_resolve_db_path(db_path)) as conn:
        conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))


def save_message(
    session_id: str,
    role: str,
    content: str | list[dict[str, Any]],
    db_path: Path | None = None,
    action_chips: str | None = None,
) -> None:
    """Persist one chat message. ``action_chips`` is a JSON-encoded list of the
    assistant turn's action-chip dicts (or None), so reopening a saved session
    restores the ✓ tool-action pills instead of bare prose."""
    text = content if isinstance(content, str) else str(content)
    now = datetime.now(UTC).isoformat()
    with _get_conn(_resolve_db_path(db_path)) as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at, action_chips) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, now, action_chips),
        )


def load_messages(session_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return []
    with _get_conn(resolved) as conn:
        rows = conn.execute(
            "SELECT role, content, action_chips FROM chat_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        msg: dict[str, Any] = {"role": row["role"], "content": row["content"]}
        raw = row["action_chips"]
        if raw:
            try:
                chips = json.loads(raw)
            except (ValueError, TypeError):
                chips = None
            if chips:
                msg["actions"] = chips
        out.append(msg)
    return out


def list_sessions(
    caller_person_id: int,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List sessions owned by `caller_person_id`, newest first.

    Legacy rows with caller_person_id IS NULL (created before this column
    existed) are excluded — the comparison `NULL = ?` never matches in
    SQLite. They remain reachable by direct session_id URL.
    """
    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return []
    with _get_conn(resolved) as conn:
        rows = conn.execute(
            """
            SELECT s.session_id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.session_id
            WHERE s.caller_person_id = ?
            GROUP BY s.session_id
            ORDER BY s.updated_at DESC
            """,
            (caller_person_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_session(session_id: str, db_path: Path | None = None) -> bool:
    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return False
    with _get_conn(resolved) as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        return cur.rowcount > 0


def get_session_metadata(session_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return None
    with _get_conn(resolved) as conn:
        row = conn.execute(
            """
            SELECT s.session_id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.session_id
            WHERE s.session_id = ?
            GROUP BY s.session_id
            """,
            (session_id,),
        ).fetchone()
    return dict(row) if row else None
