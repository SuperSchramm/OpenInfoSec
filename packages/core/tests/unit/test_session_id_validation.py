"""Issue #44: a client-supplied session id has no shape until now.

`session_id` reaches the DB, `_sessions`, `_session_starters`, and the
`chat.session_refused` / `chat.session_id_reserved` log lines -- an oversized
or control-character id is a log-injection vector and lets a caller forge an
audit key. Ported from upstream SenteLabsAI/OpenExecutive's `_clean_session_id`:
an implausible id is dropped (a fresh session is minted), not rejected with a
4xx, since a malformed id is indistinguishable from a stale client.
"""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import chat as chat_route
from openexecutive.memory import episodic, session_store
from openexecutive.memory.company_profile import CompanyProfile


@pytest.fixture(autouse=True)
def _reset_route_state() -> None:
    chat_route._sessions.clear()
    chat_route._session_starters.clear()


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    db_path = Path("./episodic_memory.db").resolve()
    monkeypatch.setattr(episodic, "DB_PATH", db_path)
    monkeypatch.setattr(session_store, "DB_PATH", db_path)
    episodic.initialize_db(db_path)
    return db_path


@pytest.fixture()
def client(db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from openexecutive.knowledge import retriever
    from openexecutive.onboarding import profile_builder
    from openexecutive.orchestrator import executive as exec_mod
    from openexecutive.utils import session_title

    monkeypatch.setattr(profile_builder, "load_or_create_profile", lambda: CompanyProfile())
    monkeypatch.setattr(retriever, "retrieve", lambda *_a, **_k: "")

    async def _no_title(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(session_title, "generate_session_title", _no_title)

    class _StubExecutive:
        _THINKING = exec_mod.Executive._THINKING

        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def stream_chat(self, **_kwargs: Any) -> AsyncIterator[str]:
            yield "ok"

        async def stream_chat_with_committee(self, **_kwargs: Any) -> AsyncIterator[str]:
            yield "ok"

    monkeypatch.setattr(exec_mod, "Executive", _StubExecutive)
    app = FastAPI()
    app.include_router(chat_route.router)
    return TestClient(app)


def _session_ids(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY rowid")]


# --------------------------------------------------------------------------- #
# openexecutive.memory.session_store.is_valid_session_id / SESSION_ID_RE
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "value",
    [
        "a" * 256,
        "abc123",
        "slack:dm:U0123",
        "discord:thread:99887766:123",
        "email:x@host",
        "a-b_c.d+e/f=g@h",
    ],
)
def test_valid_shapes_pass(value: str) -> None:
    assert session_store.is_valid_session_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 257,
        "with\nnewline",
        "with\ttab",
        "has space",
        "sneaky\r\ncontrol",
        # A trailing newline: `$` (unlike `\Z`) also matches just before one, so
        # `.match()` alone would let this through -- must use `.fullmatch()`.
        "abc\n",
        "emoji-🙂",
        "semi;colon",
        "quote'here",
    ],
)
def test_implausible_shapes_fail(value: str) -> None:
    assert not session_store.is_valid_session_id(value)


# --------------------------------------------------------------------------- #
# chat._clean_session_id
# --------------------------------------------------------------------------- #

def test_clean_session_id_passes_none_through() -> None:
    assert chat_route._clean_session_id(None) is None


def test_clean_session_id_keeps_a_plausible_id() -> None:
    assert chat_route._clean_session_id("slack:dm:U0123") == "slack:dm:U0123"


def test_clean_session_id_drops_control_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    # Assert on the module's logger directly, not via caplog: api/main.py sets
    # propagate=False on the "openexecutive" logger once any earlier test in a
    # full run has built the app, so caplog silently sees nothing then (passes
    # in isolation) -- same ambient-state trap noted in
    # test_loader_front_matter_domain.py's `loader_logger` fixture.
    from unittest.mock import MagicMock

    mock_logger = MagicMock()
    monkeypatch.setattr(chat_route, "logger", mock_logger)
    assert chat_route._clean_session_id("s1\nFAKE audit row") is None
    mock_logger.warning.assert_called_once()
    assert "chat.session_id_rejected" in mock_logger.warning.call_args[0][0]


def test_clean_session_id_drops_an_oversized_id() -> None:
    assert chat_route._clean_session_id("x" * 300) is None


# --------------------------------------------------------------------------- #
# End-to-end: a malformed id never reaches the DB or the session cache
# --------------------------------------------------------------------------- #

def test_malformed_session_id_gets_a_fresh_session_not_stored_verbatim(
    client: TestClient, db: Path
) -> None:
    evil = "s1\nchat.turn_start turn_id=fake session_id=forged"
    resp = client.post("/chat", json={"message": "hi", "session_id": evil})
    _ = resp.text
    assert resp.status_code == 200
    ids = _session_ids(db)
    assert evil not in ids
    assert evil not in chat_route._sessions
    assert evil not in chat_route._session_starters
    # A real (fresh, uuid4) session was still created so the turn works.
    assert len(ids) == 1


def test_oversized_session_id_is_rejected_by_the_model_before_the_route(
    client: TestClient,
) -> None:
    resp = client.post("/chat", json={"message": "hi", "session_id": "x" * 1000})
    assert resp.status_code == 422


def test_plausible_session_id_is_stored_verbatim(client: TestClient, db: Path) -> None:
    resp = client.post("/chat", json={"message": "hi", "session_id": "my-own-id-123"})
    _ = resp.text
    assert resp.status_code == 200
    assert _session_ids(db) == ["my-own-id-123"]


# --------------------------------------------------------------------------- #
# _session_starters growth bound
# --------------------------------------------------------------------------- #

def test_session_starters_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_route, "_SESSION_STARTERS_MAX", 3)
    for i in range(5):
        chat_route._remember_starter(f"s{i}", frozenset({"local"}))
    assert len(chat_route._session_starters) == 3
    # Oldest entries evicted first; the newest survive.
    assert set(chat_route._session_starters) == {"s2", "s3", "s4"}


def test_remember_starter_does_not_evict_when_reaffirming_an_existing_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_route, "_SESSION_STARTERS_MAX", 3)
    for i in range(3):
        chat_route._remember_starter(f"s{i}", frozenset({"local"}))
    # setdefault-style: re-adding an id already present must not evict anyone.
    chat_route._remember_starter("s0", frozenset({"other"}))
    assert set(chat_route._session_starters) == {"s0", "s1", "s2"}
    assert chat_route._session_starters["s0"] == frozenset({"local"})  # first value wins
