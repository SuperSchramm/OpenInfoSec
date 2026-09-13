"""Unit tests for route_parallel's per-specialist retrieval map."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openexecutive.orchestrator import router


@pytest.fixture
def stub_agents(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Replace SPECIALIST_REGISTRY with stubs that record what they received."""

    received: dict[str, list[dict[str, Any]]] = {}

    class StubAgent:
        def __init__(self, name: str) -> None:
            self.name = name

        async def analyze(
            self,
            query: str,
            context: str = "",
            retrieved_knowledge: str = "",
            episodic_context: str = "",
            failure_cases: str = "",
            department_memory: str = "",
            skill_context: str = "",
        ) -> str:
            received.setdefault(self.name, []).append(
                {
                    "query": query,
                    "context": context,
                    "retrieved": retrieved_knowledge,
                    "episodic": episodic_context,
                    "failures": failure_cases,
                    "department_memory": department_memory,
                    "skills": skill_context,
                }
            )
            return f"answer-from-{self.name}"

    monkeypatch.setitem(router.SPECIALIST_REGISTRY, "cfo", StubAgent("cfo"))
    monkeypatch.setitem(router.SPECIALIST_REGISTRY, "chro", StubAgent("chro"))
    return received


def test_route_parallel_auto_retrieves_per_specialist(
    stub_agents: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no map is supplied, each specialist gets its own domain-filtered RAG."""

    seen_calls: list[tuple[str, str | None]] = []

    def fake_retrieve(query: str, specialist_name: str | None = None, **_: Any) -> str:
        seen_calls.append((query, specialist_name))
        return f"<rag for {specialist_name}: {query}>"

    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.retrieve", fake_retrieve
    )
    # route_parallel now also fans out a domain-filtered failure-case
    # lookup and a skills-library lookup; stub both so the test never
    # reaches ChromaDB.
    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.retrieve_failures",
        lambda **_: "",
    )
    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.retrieve_skills",
        lambda **_: "",
    )

    results = asyncio.run(
        router.route_parallel(
            calls=[
                {"specialist": "cfo", "query": "what is our burn?"},
                {"specialist": "chro", "query": "should we layoff 10%?"},
            ]
        )
    )

    assert results == ["answer-from-cfo", "answer-from-chro"]
    assert sorted(seen_calls) == sorted([
        ("what is our burn?", "cfo"),
        ("should we layoff 10%?", "chro"),
    ])
    # Each agent receives its own RAG block, not the other specialist's.
    assert "<rag for cfo: what is our burn?>" in stub_agents["cfo"][0]["retrieved"]
    assert "<rag for chro:" in stub_agents["chro"][0]["retrieved"]
    assert "burn" not in stub_agents["chro"][0]["retrieved"]


def test_route_parallel_reuses_supplied_store_without_constructing_one(
    stub_agents: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-supplied ``store`` (e.g. app.state.store) is passed straight
    through to every retrieval path -- route_parallel must NOT construct its
    own ChromaDBStore in this case (issue #13 Phase 1: constructing a second
    client concurrently with the caller's own singleton is what crashes).
    """

    sentinel_store = object()
    seen_stores: list[Any] = []

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("route_parallel constructed its own store despite store= being supplied")

    monkeypatch.setattr("openexecutive.knowledge.store.ChromaDBStore", boom)

    def fake_retrieve(query: str, specialist_name: str | None = None, store: Any = None, **_: Any) -> str:
        seen_stores.append(store)
        return ""

    def fake_retrieve_side(store: Any = None, **_: Any) -> str:
        seen_stores.append(store)
        return ""

    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve", fake_retrieve)
    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve_failures", fake_retrieve_side)
    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve_skills", fake_retrieve_side)
    # skills_active_for also receives the shared store -- stub it so it
    # never has to distinguish the sentinel from a real store.
    monkeypatch.setattr(
        "openexecutive.knowledge.skills_index.skills_active_for",
        lambda *_a, **_k: False,
    )

    results = asyncio.run(
        router.route_parallel(
            calls=[{"specialist": "cfo", "query": "what is our burn?"}],
            store=sentinel_store,
        )
    )

    assert results == ["answer-from-cfo"]
    assert seen_stores, "expected the sentinel store to reach at least one retrieval path"
    assert all(s is sentinel_store for s in seen_stores)


def test_route_parallel_falls_back_to_process_singleton_before_constructing(
    stub_agents: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no ``store`` kwarg is passed, route_parallel should still prefer
    the process-wide mcp_server singleton (if one is running) over building
    a fresh ChromaDBStore -- the same singleton the 5 tool-handler modules
    were fixed to prefer in issue #13 Phase 1.
    """

    singleton_store = object()
    seen_stores: list[Any] = []

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("route_parallel constructed its own store despite the singleton being set")

    monkeypatch.setattr("openexecutive.knowledge.store.ChromaDBStore", boom)
    monkeypatch.setattr("openexecutive.mcp_server.server.get_store", lambda: singleton_store)

    def fake_retrieve_side(store: Any = None, **_: Any) -> str:
        seen_stores.append(store)
        return ""

    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve", fake_retrieve_side)
    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve_failures", fake_retrieve_side)
    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve_skills", fake_retrieve_side)
    monkeypatch.setattr(
        "openexecutive.knowledge.skills_index.skills_active_for",
        lambda *_a, **_k: False,
    )

    results = asyncio.run(
        router.route_parallel(calls=[{"specialist": "cfo", "query": "what is our burn?"}])
    )

    assert results == ["answer-from-cfo"]
    assert seen_stores, "expected the singleton to reach at least one retrieval path"
    assert all(s is singleton_store for s in seen_stores)


def test_route_parallel_uses_supplied_map_when_provided(
    stub_agents: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-built maps short-circuit auto-retrieval — used by tests/caching callers."""

    retrieve_called = False

    def boom(*_a: Any, **_k: Any) -> str:
        nonlocal retrieve_called
        retrieve_called = True
        return "should-not-be-used"

    monkeypatch.setattr("openexecutive.knowledge.retriever.retrieve", boom)

    asyncio.run(
        router.route_parallel(
            calls=[{"specialist": "cfo", "query": "q"}],
            retrieved_knowledge_map={"cfo": "preloaded-context"},
        )
    )

    assert not retrieve_called
    assert stub_agents["cfo"][0]["retrieved"] == "preloaded-context"


def test_route_parallel_preserves_call_order(
    stub_agents: dict[str, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order of returned results must match order of input calls for zip()."""
    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.retrieve",
        lambda **_: "ctx",
    )
    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.retrieve_failures",
        lambda **_: "",
    )
    monkeypatch.setattr(
        "openexecutive.knowledge.retriever.retrieve_skills",
        lambda **_: "",
    )

    results = asyncio.run(
        router.route_parallel(
            calls=[
                {"specialist": "chro", "query": "a"},
                {"specialist": "cfo", "query": "b"},
                {"specialist": "chro", "query": "c"},
            ]
        )
    )
    assert results == ["answer-from-chro", "answer-from-cfo", "answer-from-chro"]
