"""Regression tests for issue #16.

Four call sites deliberately replace ``app.state.store`` with a fresh
``ChromaDBStore`` after a destructive vector-store operation (fixture
unload/load, factory reset, client-slot switch). None of them refreshed the
``mcp_server`` singleton that ``orchestrator.store_access.get_shared_store()``
reads for tool handlers with no Request/app access — leaving it pointing at
the pre-swap store indefinitely. Each site now also calls
``mcp_server.set_store()`` inside the same swap.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.cli import fixture_loader
from openexecutive.clients import slots
from openexecutive.knowledge.store import ChromaDBStore
from openexecutive.mcp_server import server as mcp_server


@pytest.fixture(autouse=True)
def _reset_mcp_singleton() -> Any:
    """Isolate the process-wide mcp_server store singleton per test."""
    mcp_server.set_store(None)
    yield
    mcp_server.set_store(None)


# --------------------------------------------------------------------------- #
# api/routes/fixtures.py — fixtures_unload / load_fixture
# --------------------------------------------------------------------------- #

@pytest.fixture
def _fixtures_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from openexecutive.api.routes import fixtures as fixtures_route

    monkeypatch.setattr(
        "openexecutive.config.get_settings",
        lambda: type("S", (), {"vector_store_path": "unused"})(),
    )
    app = FastAPI()
    app.include_router(fixtures_route.router)
    app.state.store = object()  # pre-swap sentinel
    return TestClient(app)


def test_fixtures_unload_refreshes_mcp_server_singleton(
    _fixtures_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openexecutive.cli.fixture_loader.unload_fixture",
        lambda settings: _async_result({"unloaded": True}),
    )
    monkeypatch.setattr(ChromaDBStore, "__init__", lambda self, **_kw: None)

    resp = _fixtures_client.post("/fixtures/unload")

    assert resp.status_code == 200
    assert mcp_server.get_store() is not None
    assert mcp_server.get_store() is _fixtures_client.app.state.store


def test_load_fixture_refreshes_mcp_server_singleton(
    _fixtures_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openexecutive.cli.fixture_loader.load_fixture_any",
        lambda name, settings: _async_result({"loaded": name}),
    )
    monkeypatch.setattr(ChromaDBStore, "__init__", lambda self, **_kw: None)

    resp = _fixtures_client.post("/fixtures/demo-startup/load")

    assert resp.status_code == 200
    assert mcp_server.get_store() is not None
    assert mcp_server.get_store() is _fixtures_client.app.state.store


async def _async_result(value: Any) -> Any:
    return value


# --------------------------------------------------------------------------- #
# cli/fixture_loader.py — reset_all_state
# --------------------------------------------------------------------------- #

@pytest.fixture()
def _reset_settings_stub(tmp_path: Path) -> Any:
    profile_path = tmp_path / "company" / "profile.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("name: ''\n")
    return type(
        "S",
        (),
        {
            "vector_store_path": tmp_path / "chroma",
            "company_profile_path": profile_path,
            "honcho_workspace_id": "openexec",
        },
    )()


@pytest.fixture(autouse=True)
def _isolate_reset_dbs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every DB reset_all_state touches at tmp_path and init schemas."""
    from openexecutive.departments import store as dept_store
    from openexecutive.memory import episodic
    from openexecutive.people import store as people_store

    episodic_path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", episodic_path)
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "people.db")
    monkeypatch.setattr(dept_store, "DB_PATH", tmp_path / "depts.db")
    episodic.initialize_db(episodic_path)

    from openexecutive.alerts import store as alerts_store
    from openexecutive.audit.logger import AuditLogger
    from openexecutive.evals.persistence import (
        initialize_eval_runs_db,
        initialize_user_scenarios_db,
    )
    from openexecutive.workflows.persistence import initialize_runs_db

    alerts_store.initialize_db(episodic_path)
    initialize_runs_db(episodic_path)
    initialize_eval_runs_db(episodic_path)
    initialize_user_scenarios_db(episodic_path)
    AuditLogger(db_path=episodic_path)
    people_store.initialize_db()
    dept_store.initialize_db()


@pytest.fixture(autouse=True)
def _stub_honcho_for_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.memory import honcho_client

    async def _noop(workspace_id: str | None = None) -> None:
        return None

    monkeypatch.setattr(honcho_client, "delete_workspace_and_reset_client", _noop)


def test_reset_all_state_refreshes_mcp_server_singleton(
    _reset_settings_stub: Any,
) -> None:
    class _AppState:
        pass

    app_state = _AppState()
    app_state.store = object()  # pre-swap sentinel

    with (
        patch.object(ChromaDBStore, "delete_company_docs", lambda self: None),
        patch.object(ChromaDBStore, "delete_documents", lambda self, **kw: None),
    ):
        result = asyncio.run(
            fixture_loader.reset_all_state(_reset_settings_stub, app_state=app_state)
        )

    assert result["reset"] is True
    assert mcp_server.get_store() is app_state.store


# --------------------------------------------------------------------------- #
# clients/slots.py — _rebuild_vector_state
# --------------------------------------------------------------------------- #

def test_rebuild_vector_state_refreshes_mcp_server_singleton(tmp_path: Path) -> None:
    profile_path = tmp_path / "company" / "profile.yaml"
    docs_dir = profile_path.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    settings = type(
        "S",
        (),
        {
            "vector_store_path": tmp_path / "chroma",
            "company_profile_path": profile_path,
        },
    )()

    class _AppState:
        pass

    app_state = _AppState()
    app_state.store = object()  # pre-swap sentinel

    with (
        patch.object(ChromaDBStore, "delete_company_docs", lambda self: None),
        patch.object(ChromaDBStore, "delete_documents", lambda self, **kw: None),
        patch.object(ChromaDBStore, "delete_notion_docs", lambda self: None),
    ):
        docs_indexed = asyncio.run(slots._rebuild_vector_state(settings, app_state))

    assert docs_indexed == 0  # no docs in the empty tmp docs dir
    assert mcp_server.get_store() is app_state.store


def test_rebuild_vector_state_refreshes_singleton_even_with_no_app_state(
    tmp_path: Path,
) -> None:
    """Issue #15 follow-up (found by adversarial review): the scheduler's
    client-rotation path calls this with app_state=None, but still runs
    in-process under the API's lifespan. The singleton refresh must not be
    skipped just because there's no app_state.store to also update —
    before this fix, an automated rotation left mcp_server's singleton
    (and therefore every no-Request tool handler) pointing at the
    pre-rotation client's store indefinitely."""
    profile_path = tmp_path / "company" / "profile.yaml"
    docs_dir = profile_path.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    settings = type(
        "S",
        (),
        {
            "vector_store_path": tmp_path / "chroma",
            "company_profile_path": profile_path,
        },
    )()

    original_init = ChromaDBStore.__init__
    constructed: list[Any] = []

    def _tracking_init(self: Any, *a: Any, **k: Any) -> None:
        constructed.append(self)
        original_init(self, *a, **k)

    with (
        patch.object(ChromaDBStore, "__init__", _tracking_init),
        patch.object(ChromaDBStore, "delete_company_docs", lambda self: None),
        patch.object(ChromaDBStore, "delete_documents", lambda self, **kw: None),
        patch.object(ChromaDBStore, "delete_notion_docs", lambda self: None),
    ):
        asyncio.run(slots._rebuild_vector_state(settings, None))

    # Exactly one ChromaDBStore built (not a second one for the publish
    # step — reviewer observation: publish_swapped_store going unconditional
    # meant this path went from 0-or-1 to always-2 constructions until the
    # swap call was pointed at the same `store` already used for the
    # delete/reindex work above).
    assert len(constructed) == 1
    assert mcp_server.get_store() is constructed[0]
