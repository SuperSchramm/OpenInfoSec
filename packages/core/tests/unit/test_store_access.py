"""Unit tests for the shared ChromaDBStore accessor used by orchestrator
tool handlers (issue #13 Phase 1)."""
from __future__ import annotations

from typing import Any

import pytest

from openexecutive.mcp_server import server as mcp_server
from openexecutive.orchestrator import store_access

# The swap-generation reset fixture (issue #26) lives in tests/conftest.py
# as an autouse fixture alongside reset_active_gateway/reset_active_store --
# it applies here automatically.


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


# --------------------------------------------------------------------------- #
# publish_swapped_store (issue #16)
# --------------------------------------------------------------------------- #

def test_publish_swapped_store_updates_both_app_state_and_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(mcp_server, "set_store", captured.append)

    class _AppState:
        store = "stale"

    app_state = _AppState()
    new_store = object()

    store_access.publish_swapped_store(app_state, new_store)

    assert app_state.store is new_store
    assert captured == [new_store]


def test_publish_swapped_store_still_refreshes_singleton_when_app_state_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #15 follow-up: the scheduler's client-rotation path calls the
    swap site with no app_state, but still runs in-process under the API's
    lifespan — the singleton refresh must not be skipped just because
    there's no app_state.store to also update."""
    captured: list[Any] = []
    monkeypatch.setattr(mcp_server, "set_store", captured.append)

    new_store = object()
    store_access.publish_swapped_store(None, new_store)  # must not raise

    assert captured == [new_store]


def test_publish_swapped_store_still_refreshes_singleton_when_app_state_has_no_store_attr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(mcp_server, "set_store", captured.append)

    class _BareAppState:
        pass

    bare = _BareAppState()
    new_store = object()
    store_access.publish_swapped_store(bare, new_store)  # must not raise

    assert captured == [new_store]
    assert not hasattr(bare, "store")  # nothing to update, correctly skipped


# --------------------------------------------------------------------------- #
# Swap generation counter (issue #26)
# --------------------------------------------------------------------------- #

def test_get_store_generation_starts_at_zero_after_reset() -> None:
    assert store_access.get_store_generation() == 0


def test_bump_store_generation_increments_and_returns_new_value() -> None:
    assert store_access.bump_store_generation() == 1
    assert store_access.bump_store_generation() == 2
    assert store_access.get_store_generation() == 2


def test_publish_swapped_store_bumps_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for issue #26: every company-switch site calls
    publish_swapped_store(), so bumping the generation there (rather than
    at each of the 3 call sites individually) is what actually invalidates
    an in-flight background attachment ingest."""
    monkeypatch.setattr(mcp_server, "set_store", lambda *_a: None)
    before = store_access.get_store_generation()

    store_access.publish_swapped_store(None, object())

    assert store_access.get_store_generation() == before + 1


def test_publish_swapped_store_bumps_generation_on_every_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "set_store", lambda *_a: None)

    store_access.publish_swapped_store(None, object())
    store_access.publish_swapped_store(None, object())
    store_access.publish_swapped_store(None, object())

    assert store_access.get_store_generation() == 3
