"""Regression tests for issue #29: company docs tagged "general" must be
visible to every specialist's domain-scoped retrieval.

"general" is ingest's fallback when nothing says otherwise (a default
/documents upload, a fixture doc). Every specialist's company query used to be
scoped to its own domains only, so those docs were reachable solely through
the Executive's own unscoped query -- the specialists never saw the company's
own policies. Only COMPANY is widened: NOTION (multi-writer, unreviewed) is
deliberately not, and the tests below pin that.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openexecutive.knowledge.retriever import _with_general, retrieve
from openexecutive.knowledge.store import ChromaDBStore


@pytest.fixture
def fake_review_store() -> SimpleNamespace:
    return SimpleNamespace(
        get_rejected_filenames=lambda _ct: set(),
        get_rejected_source_ids=lambda: set(),
        get_priority_map=lambda _ct: {},
        list_annotations=lambda domains=None, active_only=True: [],
    )


def test_with_general_appends_general_once() -> None:
    assert _with_general(["security"]) == ["security", "general"]
    assert _with_general(["security", "governance"]) == ["security", "governance", "general"]
    assert _with_general(["general"]) == ["general"]
    assert _with_general(["security", "general"]) == ["security", "general"]


def test_with_general_leaves_an_unscoped_query_unscoped() -> None:
    # None means "no domain filter" -- already sees everything, must stay that way.
    assert _with_general(None) is None


def test_with_general_does_not_mutate_its_input() -> None:
    domains = ["security"]
    _with_general(domains)
    assert domains == ["security"]


def _recording_store() -> tuple[MagicMock, dict[str, Any]]:
    seen: dict[str, Any] = {}
    store = MagicMock()

    def fake_query(*, query_text: str, collection: str, domain_filter: Any, n_results: int) -> list:
        seen[collection] = domain_filter
        return []

    store.query.side_effect = fake_query
    return store, seen


def test_specialist_scope_widens_company_only(
    fake_review_store: SimpleNamespace,
) -> None:
    store, seen = _recording_store()

    retrieve(
        query="What is our incident response process?",
        specialist_name="cyberops",
        store=store,
        review_store=fake_review_store,
    )

    assert seen[ChromaDBStore.COMPANY_COLLECTION] == ["security", "general"]
    # NOTION is multi-writer and unreviewed, and notion_sync.infer_domain falls
    # back to "general" for any page whose TITLE has no domain keyword -- so a
    # writer could otherwise put a page in front of every specialist just by
    # titling it blandly (issue #29 security review). It stays strictly scoped.
    assert seen[ChromaDBStore.NOTION_COLLECTION] == ["security"]
    # BUILTIN keeps its strict scoping: its "general" bucket is uncurated leftovers.
    assert seen[ChromaDBStore.BUILTIN_COLLECTION] == ["security"]
    # Attachments and research were already never domain-scoped.
    assert seen[ChromaDBStore.ATTACHMENT_COLLECTION] is None
    assert seen[ChromaDBStore.RESEARCH_COLLECTION] is None


def test_explicit_domain_filter_is_widened_the_same_way(
    fake_review_store: SimpleNamespace,
) -> None:
    # Workflows pass an explicit domain_filter rather than a specialist name.
    store, seen = _recording_store()

    retrieve(
        query="Summarize our pricing approach",
        domain_filter=["finance"],
        store=store,
        review_store=fake_review_store,
    )

    assert seen[ChromaDBStore.COMPANY_COLLECTION] == ["finance", "general"]
    assert seen[ChromaDBStore.BUILTIN_COLLECTION] == ["finance"]


def test_unscoped_retrieve_stays_unscoped(fake_review_store: SimpleNamespace) -> None:
    store, seen = _recording_store()

    retrieve(query="What is our incident response process?", store=store, review_store=fake_review_store)

    assert seen[ChromaDBStore.COMPANY_COLLECTION] is None
    assert seen[ChromaDBStore.NOTION_COLLECTION] is None


def test_real_store_round_trip_general_doc_reaches_a_domain_scoped_specialist(
    tmp_path: Path, fake_review_store: SimpleNamespace
) -> None:
    """End to end against a real Chroma collection -- the exact scenario from
    issue #29 (a security specialist asking about a doc tagged "general")."""
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    incident = "Severity one incident response plan: isolate the host, preserve evidence, page the CISO."
    budget = "Quarterly marketing budget allocation for paid social campaigns and events."
    store.add_documents(
        texts=[incident, budget],
        metadatas=[
            {"domain": "general", "filename": "incident_response_plan.md"},
            {"domain": "finance", "filename": "budget.md"},
        ],
        ids=["ir-1", "bud-1"],
        collection=ChromaDBStore.COMPANY_COLLECTION,
    )

    out = retrieve(
        query=incident,
        specialist_name="cyberops",  # domain scope: ["security"] (+ "general" after the fix)
        store=store,
        review_store=fake_review_store,
        n_builtin=0,
    )

    assert "incident_response_plan.md" in out
    # A doc tagged with a *different* specific domain must stay out of scope.
    assert "budget.md" not in out
