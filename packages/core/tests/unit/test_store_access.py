"""Unit tests for the shared ChromaDBStore accessor used by orchestrator
tool handlers (issue #13 Phase 1)."""
from __future__ import annotations

from typing import Any

import pytest

from openexecutive.mcp_server import server as mcp_server
from openexecutive.orchestrator import store_access


def test_get_shared_store_prefers_the_process_wide_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    singleton = object()
    monkeypatch.setattr(mcp_server, "get_store", lambda: singleton)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("get_shared_store() constructed a store despite the singleton being set")

    monkeypatch.setattr("openexecutive.knowledge.store.ChromaDBStore", boom)

    assert store_access.get_shared_store() is singleton


def test_get_shared_store_falls_back_to_construction_when_no_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_store", lambda: None)
    fresh = object()
    monkeypatch.setattr("openexecutive.knowledge.store.ChromaDBStore", lambda **_k: fresh)

    assert store_access.get_shared_store() is fresh
