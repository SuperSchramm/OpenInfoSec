from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openexecutive.memory.episodic import _get_conn, get_episodic_db_path

DB_PATH = get_episodic_db_path()

# A session id reaches every audit and usage row a turn produces, and the log
# lines chat.py emits about it (chat.session_refused, chat.session_id_reserved,
# ...), so an unconstrained value is a log-injection vector (a newline forges a
# log record) and makes cross-caller collisions trivial to engineer. Wide
# enough for every real shape this app mints or accepts: a web uuid4, and the
# channel-namespaced ids adapters build (`slack:thread:C1:1700000000.001`,
# `email:x@host`). Shared by chat.py (client-supplied ids) and audit.py (the
# session_id query filter) so the charset can't drift between the write and
# read side (issue #44; ported from upstream SenteLabsAI/OpenExecutive).
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_:@\-.+/=]{1,256}$")


def is_valid_session_id(session_id: str) -> bool:
    # fullmatch, not match: `$` (unlike `\Z`) also matches just before a single
    # trailing newline, so `.match()` alone would let "abc\n" through -- the
    # exact log-injection shape this check exists to keep out (found by review).
    return bool(SESSION_ID_RE.fullmatch(session_id))


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
    SQLite. Only the principal can still open them by id (see
    `api.routes.chat._session_access`).
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


def get_session_owner(session_id: str, db_path: Path | None = None) -> tuple[bool, int | None]:
    """``(exists, caller_person_id)`` for one session."""
    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return False, None
    with _get_conn(resolved) as conn:
        row = conn.execute(
            "SELECT caller_person_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return False, None
    owner = row["caller_person_id"]
    return True, (int(owner) if owner is not None else None)


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
