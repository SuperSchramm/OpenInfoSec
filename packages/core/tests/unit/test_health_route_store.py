"""Unit tests for GET /health — issue #17 regression guard.

health_check() constructed its own ChromaDBStore() instead of reusing
request.app.state.store the way every other route handler in the package
does.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import health as health_route
from openexecutive.knowledge.store import ChromaDBStore


class _FakeStore:
    """Doubles as a fake store instance AND, via **_kwargs in __init__, as a
    drop-in replacement for the ChromaDBStore *class* itself — health.py
    reads ChromaDBStore.BUILTIN_COLLECTION as a class attribute in the same
    scope where it may also call ChromaDBStore(persist_directory=...), so a
    bare lambda replacement (constructor-only) isn't enough."""

    BUILTIN_COLLECTION = ChromaDBStore.BUILTIN_COLLECTION
    last_instance: _FakeStore | None = None

    def __init__(self, **_kwargs: object) -> None:
        self.queried_collection: str | None = None
        type(self).last_instance = self

    def get_collection_count(self, collection: str) -> int:
        self.queried_collection = collection
        return 7


@pytest.fixture(autouse=True)
def _required_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("EXEC_EMAIL_ADDRESS", "exec@example.com")
    monkeypatch.setenv("COMPANY_PROFILE_PATH", str(tmp_path / "company" / "profile.yaml"))
    # Pinned even though the fallback test replaces ChromaDBStore entirely —
    # if that monkeypatch ever stopped applying, an unpinned VECTOR_STORE_PATH
    # would silently fall through to constructing a real store against the
    # repo's on-disk chroma_db instead of failing loudly.
    monkeypatch.setenv("VECTOR_STORE_PATH", str(tmp_path / "chroma_db"))
    monkeypatch.setattr(
        "openexecutive.knowledge.skills_index.count_skills",
        lambda store, source=None: 0,
    )
    _FakeStore.last_instance = None


def test_health_uses_app_state_store_when_present() -> None:
    """The process-wide store set in api/main.py's lifespan must be reused,
    not shadowed by a fresh construction."""
    fake_store = _FakeStore()
    app = FastAPI()
    app.include_router(health_route.router)
    app.state.store = fake_store

    resp = TestClient(app).get("/health")

    assert resp.status_code == 200
    assert fake_store.queried_collection == _FakeStore.BUILTIN_COLLECTION
    assert _FakeStore.last_instance is fake_store, "must not construct a fresh store alongside app.state.store"


def test_health_falls_back_to_fresh_store_when_app_state_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare test app that never ran the lifespan (no app.state.store) must
    not crash — falls back to constructing a store, same as this endpoint's
    pre-fix behavior, just no longer the only path."""
    monkeypatch.setattr("openexecutive.knowledge.store.ChromaDBStore", _FakeStore)

    app = FastAPI()
    app.include_router(health_route.router)
    # Deliberately do NOT set app.state.store.

    resp = TestClient(app).get("/health")

    assert resp.status_code == 200
    assert _FakeStore.last_instance is not None, "fallback path never constructed a store"
    assert _FakeStore.last_instance.queried_collection == _FakeStore.BUILTIN_COLLECTION
