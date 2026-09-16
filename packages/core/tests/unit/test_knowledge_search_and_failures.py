"""Smoke tests for /knowledge/search and /knowledge/failures routes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-used")

from fastapi.testclient import TestClient  # noqa: E402

from openexecutive.api.main import create_app  # noqa: E402


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """App with a fake Chroma store and an empty failures tree under tmp_path."""
    # Redirect the failures CRUD to a temp tree so we don't write into the repo.
    failures_root = tmp_path / "failures"
    failures_root.mkdir(parents=True, exist_ok=True)
    (failures_root / "strategy").mkdir()
    (failures_root / "strategy" / "kodak-digital.md").write_text("# Kodak\n\nFilm margins.\n")

    monkeypatch.setattr(
        "openexecutive.api.routes.knowledge.FAILURES_KNOWLEDGE_PATH",
        failures_root,
    )

    # Per-collection query stubs return distinct rows so we can verify partitioning.
    def fake_query(
        query_text: str,
        collection: str,
        domain_filter: list[str] | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        if collection == "builtin_knowledge":
            return [
                {
                    "text": "Playbook chunk about strategy.",
                    "metadata": {
                        "filename": "product_strategy.md",
                        "domain": "strategy",
                        "source": "/abs/product_strategy.md",
                        "chunk_index": 0,
                        "type": "builtin",
                    },
                    "distance": 0.30,
                },
                {
                    "text": "OpenStax finance excerpt.",
                    "metadata": {
                        "filename": "openstax-finance.pdf",
                        "domain": "finance",
                        "source_id": "openstax-finance",
                        "source_url": "https://openstax.org/x",
                        "license": "CC BY 4.0",
                        "publisher": "OpenStax",
                        "chunk_index": 5,
                    },
                    "distance": 0.41,
                },
            ]
        if collection == "company_docs":
            return [
                {
                    "text": "Internal deck text.",
                    "metadata": {
                        "filename": "deck.pdf",
                        "domain": "strategy",
                        "chunk_index": 2,
                    },
                    "distance": 0.5,
                }
            ]
        if collection == "failure_cases":
            return [
                {
                    "text": "Kodak shelved digital.",
                    "metadata": {
                        "filename": "kodak-digital.md",
                        "domain": "strategy",
                        "chunk_index": 0,
                        "type": "failure_case",
                    },
                    "distance": 0.42,
                }
            ]
        if collection == "attachment_uploads":
            return [
                {
                    "text": "A user's Discord attachment about strategy.",
                    "metadata": {
                        "filename": "upload.md",
                        "domain": "strategy",
                        "chunk_index": 0,
                        "type": "attachment",
                    },
                    "distance": 0.35,
                }
            ]
        return []

    fake_store = MagicMock()
    fake_store.query.side_effect = fake_query
    monkeypatch.setattr(
        "openexecutive.api.routes.knowledge._get_store",
        lambda _request: fake_store,
    )

    return TestClient(create_app())


def test_search_partitions_builtin_and_external(client: TestClient) -> None:
    res = client.post("/knowledge/search", json={"query": "strategy"})
    assert res.status_code == 200
    data = res.json()
    # Rows with source_id should land in `external`, rows without in `builtin`.
    builtin_files = [h["filename"] for h in data["builtin"]]
    external_files = [h["filename"] for h in data["external"]]
    assert builtin_files == ["product_strategy.md"]
    assert external_files == ["openstax-finance.pdf"]
    assert data["company"][0]["filename"] == "deck.pdf"
    assert data["failures"][0]["filename"] == "kodak-digital.md"
    assert data["attachment"][0]["filename"] == "upload.md"


def test_search_attachment_bucket_can_be_excluded(client: TestClient) -> None:
    """Regression test for issue #25 step 1's security-review finding: an
    operator must be able to search/enumerate ATTACHMENT_COLLECTION now
    that it no longer shares COMPANY_COLLECTION with curated uploads —
    without this bucket there was no detection surface for what an
    attacker-influenced attachment upload actually put into the shared
    knowledge base."""
    res = client.post(
        "/knowledge/search", json={"query": "x", "include": ["attachment"]}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["builtin"] == []
    assert data["company"] == []
    assert data["failures"] == []
    assert data["external"] == []
    assert len(data["attachment"]) == 1
    assert data["attachment"][0]["filename"] == "upload.md"


def test_search_specialist_filters_to_domains(client: TestClient) -> None:
    res = client.post("/knowledge/search", json={"query": "pricing", "specialist": "cfo"})
    assert res.status_code == 200
    data = res.json()
    assert data["effective_domains"] == ["finance"]
    # cfo only sees finance; cpo sees product+strategy, so it isn't listed.
    assert "cfo" in data["specialists_that_would_see_this"]
    assert "cpo" not in data["specialists_that_would_see_this"]


def test_search_rejects_unknown_specialist(client: TestClient) -> None:
    res = client.post(
        "/knowledge/search", json={"query": "x", "specialist": "ghost"}
    )
    assert res.status_code == 400


def test_search_rejects_empty_query(client: TestClient) -> None:
    res = client.post("/knowledge/search", json={"query": "   "})
    assert res.status_code == 400


def test_search_include_filter_skips_collections(client: TestClient) -> None:
    res = client.post(
        "/knowledge/search", json={"query": "x", "include": ["failures"]}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["builtin"] == []
    assert data["company"] == []
    assert data["external"] == []
    assert len(data["failures"]) == 1


def test_search_partition_does_not_starve_external_under_builtin_dominance(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """When the BUILTIN_COLLECTION top-K is mostly builtin, external must still appear."""

    # 12 builtin chunks closer than the 1 external chunk. With a too-small
    # overfetch window the external chunk would be invisible.
    def dominated_query(
        query_text: str,
        collection: str,
        domain_filter: list[str] | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        if collection == "builtin_knowledge":
            rows = [
                {
                    "text": f"builtin row {i}",
                    "metadata": {"filename": f"b{i}.md", "domain": "strategy", "chunk_index": i},
                    "distance": 0.10 + i * 0.01,
                }
                for i in range(12)
            ]
            rows.append(
                {
                    "text": "external row",
                    "metadata": {
                        "filename": "ext.md",
                        "domain": "strategy",
                        "source_id": "openstax-x",
                        "publisher": "OpenStax",
                        "chunk_index": 0,
                    },
                    "distance": 0.50,
                }
            )
            return rows[:n_results]
        return []

    fake_store = MagicMock()
    fake_store.query.side_effect = dominated_query
    monkeypatch.setattr(
        "openexecutive.api.routes.knowledge._get_store",
        lambda _request: fake_store,
    )

    res = client.post(
        "/knowledge/search",
        json={"query": "x", "n_builtin": 3, "n_external": 1, "include": ["builtin", "external"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["builtin"]) == 3
    assert len(data["external"]) == 1
    assert data["external"][0]["filename"] == "ext.md"


def test_search_rejects_invalid_include(client: TestClient) -> None:
    res = client.post(
        "/knowledge/search", json={"query": "x", "include": ["bogus"]}
    )
    assert res.status_code == 400


def test_failures_list_and_get(client: TestClient) -> None:
    res = client.get("/knowledge/failures")
    assert res.status_code == 200
    files = res.json()["files"]
    assert any(f["filename"] == "kodak-digital.md" for f in files)

    res = client.get("/knowledge/failures/strategy/kodak-digital.md")
    assert res.status_code == 200
    body = res.json()
    assert body["domain"] == "strategy"
    assert "Kodak" in body["content"]


def test_failures_rejects_bad_domain(client: TestClient) -> None:
    res = client.get("/knowledge/failures/notadomain/foo.md")
    assert res.status_code == 400


def test_failures_rejects_path_traversal(client: TestClient) -> None:
    res = client.get("/knowledge/failures/strategy/..%2Fevil.md")
    # The path-segment regex requires a *.md and no slashes/dots-as-traversal,
    # so the encoded "../evil.md" must be rejected as a bad filename.
    assert res.status_code in (400, 404)


# --------------------------------------------------------------------------- #
# DELETE /knowledge/attachments — manual admin purge (issue #25 step 2)
# --------------------------------------------------------------------------- #

def test_purge_attachments_requires_shared_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same auth model as every other mutating route in this API: the
    app-wide shared-secret middleware. Mirrors test_mcp_server.py's
    test_mcp_endpoint_is_gated_by_shared_secret — routing only, lifespan is
    not run, so no DB/store setup is needed for this check."""
    monkeypatch.setenv("BACKEND_SHARED_SECRET", "testsecret")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    app = create_app()
    test_client = TestClient(app)  # not a context manager → lifespan does not run

    no_key = test_client.delete("/knowledge/attachments", follow_redirects=False)
    assert no_key.status_code == 401

    with_key = test_client.delete(
        "/knowledge/attachments",
        headers={"x-api-key": "testsecret"},
        follow_redirects=False,
    )
    assert with_key.status_code != 401


def test_purge_attachments_wipes_collection_and_returns_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for issue #25 step 2 logic review round 2: must
    count BEFORE deleting, not after — a delete-then-count order would
    always report 0 against a real store, since the collection is already
    gone by the time the count runs. assert_has_calls(..., any_order=False)
    pins both the calls AND their order in one standard-library assertion."""
    fake_store = MagicMock()
    fake_store.get_collection_count.return_value = 7
    monkeypatch.setattr(
        "openexecutive.api.routes.knowledge._get_store",
        lambda _request: fake_store,
    )

    res = client.delete("/knowledge/attachments")

    assert res.status_code == 200
    assert res.json() == {"purged": 7}
    fake_store.assert_has_calls(
        [call.get_collection_count("attachment_uploads"), call.delete_attachment_docs()],
        any_order=False,
    )
