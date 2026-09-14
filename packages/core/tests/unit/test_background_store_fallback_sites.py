"""Regression tests for issue #15.

11 direct ``ChromaDBStore(...)`` construction sites bypassed
``route_parallel``/tool-handler dispatch entirely, so Phase 1's singleton fix
(issue #13) never reached them. Each site's ``store is None`` fallback now
calls ``orchestrator.store_access.get_shared_store()`` instead of
constructing a fresh, unconfigured-relative-to-settings store.

None of these fallback branches were previously exercised by any existing
test — every existing caller in the suite passes ``store=`` explicitly, so
"existing tests still pass" gave zero assurance about these specific lines.
These tests call each site with no ``store`` argument and confirm
``get_shared_store()`` is what actually gets used.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_review_store() -> SimpleNamespace:
    return SimpleNamespace(
        get_rejected_filenames=lambda _ct: set(),
        get_rejected_source_ids=lambda: set(),
        get_priority_map=lambda _ct: {},
        list_annotations=lambda domains=None, active_only=True: [],
    )


def _make_store(hits: list[dict[str, Any]] | None = None) -> MagicMock:
    store = MagicMock()
    store.query.return_value = hits or []
    return store


def _patch_get_shared_store(
    monkeypatch: pytest.MonkeyPatch, shared: Any
) -> list[int]:
    """Patch get_shared_store() to return `shared`, returning a list whose
    length is the call count — a stronger signal than inspecting `shared`
    for side effects, since some callers (retrieve_skills) swallow all
    downstream exceptions and would otherwise mask a broken store."""
    calls: list[int] = []

    def _fake() -> Any:
        calls.append(1)
        return shared

    monkeypatch.setattr("openexecutive.orchestrator.store_access.get_shared_store", _fake)
    return calls


def _forbid_direct_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make retriever.py's module-level ChromaDBStore raise if constructed
    directly — proves the fallback goes through get_shared_store() and
    doesn't also (or instead) build its own store. Subclasses the real
    class so class-attribute access elsewhere in the module (e.g.
    ChromaDBStore.BUILTIN_COLLECTION) keeps working; only construction is
    blocked."""
    from openexecutive.knowledge.store import ChromaDBStore

    class _GuardedChromaDBStore(ChromaDBStore):
        def __new__(cls, *_a: Any, **_k: Any) -> _GuardedChromaDBStore:
            raise AssertionError(
                "retriever.py constructed a ChromaDBStore directly instead "
                "of calling get_shared_store()"
            )

    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.ChromaDBStore", _GuardedChromaDBStore
    )


# --------------------------------------------------------------------------- #
# knowledge/retriever.py — 3 sites
# --------------------------------------------------------------------------- #

def test_retrieve_uses_shared_store_when_none_passed(
    fake_review_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openexecutive.knowledge import retriever

    shared = _make_store()
    calls = _patch_get_shared_store(monkeypatch, shared)
    _forbid_direct_construction(monkeypatch)

    retriever.retrieve(
        query="a real question about company strategy and market position",
        store=None,
        review_store=fake_review_store,
    )

    assert calls == [1]
    assert shared.query.called


def test_retrieve_failures_uses_shared_store_when_none_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.knowledge import retriever

    shared = _make_store()
    calls = _patch_get_shared_store(monkeypatch, shared)
    _forbid_direct_construction(monkeypatch)

    retriever.retrieve_failures(
        query="a real question about a past incident", store=None
    )

    assert calls == [1]
    assert shared.query.called


def test_retrieve_skills_uses_shared_store_when_none_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.knowledge import retriever

    shared = _make_store()
    calls = _patch_get_shared_store(monkeypatch, shared)
    _forbid_direct_construction(monkeypatch)

    # retrieve_skills wraps its ChromaDB access in a broad try/except (any
    # gate/search failure is "nothing found," never propagated), so a
    # MagicMock store that doesn't perfectly shape its return value can't
    # crash this call. That's exactly why get_shared_store()'s call count
    # (not a side effect on `shared`, which the try/except could swallow)
    # is the assertion that actually proves the fallback ran.
    retriever.retrieve_skills(
        "a real question long enough to clear the short-query gate",
        specialist_name="ciso",
        store=None,
    )

    assert calls == [1]

    assert shared.method_calls, "shared store was never touched by retrieve_skills"


# --------------------------------------------------------------------------- #
# clients/rotation.py — _run_quiet_work_for_live_client
# --------------------------------------------------------------------------- #

async def test_quiet_work_uses_shared_store(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openexecutive.clients import rotation

    class _FakeEvent:
        type = "artifact"
        content = "done"
        message = ""

    class _FakeWorkflow:
        name = "morning_brief"
        title = "Morning Brief"

        def input_model(self) -> type:
            class _Inputs:
                def model_dump(self) -> dict[str, Any]:
                    return {}

            return _Inputs

        async def run(self, inputs: Any, store: Any):
            _FakeWorkflow.received_store = store
            yield _FakeEvent()

    shared = object()
    monkeypatch.setattr(
        "openexecutive.orchestrator.store_access.get_shared_store", lambda: shared
    )
    monkeypatch.setattr(
        "openexecutive.workflows.WORKFLOW_REGISTRY", {"morning_brief": _FakeWorkflow()}
    )
    monkeypatch.setattr("openexecutive.workflows.persistence.create_run", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.workflows.persistence.complete_run", lambda *a, **k: None)

    settings = SimpleNamespace(
        vector_store_path=tmp_path / "chroma", external_monitor_enabled=False
    )
    await rotation._run_quiet_work_for_live_client(settings, "acme")

    assert _FakeWorkflow.received_store is shared


# --------------------------------------------------------------------------- #
# architecture/facts.py — _collect_health
# --------------------------------------------------------------------------- #

def test_collect_health_uses_shared_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.architecture import facts

    shared = MagicMock()
    shared.get_collection_count.return_value = 42
    monkeypatch.setattr(
        "openexecutive.orchestrator.store_access.get_shared_store", lambda: shared
    )

    snapshot = facts._collect_health()

    assert snapshot.get("builtin_knowledge_chunks") == 42
    assert shared.get_collection_count.called
