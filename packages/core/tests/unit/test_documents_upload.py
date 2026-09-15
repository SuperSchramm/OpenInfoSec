"""Unit tests for POST /documents — domain must come from the multipart form.

Regression guard: `domain` was declared as a bare default (`domain: str =
"general"`), which FastAPI parses as a query parameter. The UI sends it as a
form field, so it was silently dropped and every upload landed under
"general" — invisible to domain-filtered specialist retrieval.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import documents


class _CapturingStore:
    """Realistic-enough in-memory ChromaDB double: upsert-by-id and
    delete-by-metadata-filter, so tests can assert actual overwrite/delete
    behavior (`self.documents`, current live state) rather than only the
    arguments a single call received. `added`/`added_ids` stay as an
    append-only call history for tests that just want "was this ever
    added with domain=X", independent of later overwrites."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.added: list[dict[str, Any]] = []
        self.added_ids: list[str] = []

    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        collection: str,
    ) -> None:
        self.added.extend(metadatas)
        self.added_ids.extend(ids)
        for text, meta, doc_id in zip(texts, metadatas, ids, strict=False):
            self.documents[doc_id] = {"text": text, "metadata": meta}

    def delete_documents(self, collection: str, where: dict[str, Any]) -> None:
        for doc_id in [
            doc_id
            for doc_id, doc in self.documents.items()
            if all(doc["metadata"].get(k) == v for k, v in where.items())
        ]:
            del self.documents[doc_id]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VECTOR_STORE_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("COMPANY_PROFILE_PATH", str(tmp_path / "company" / "profile.yaml"))
    # Don't fan out to the real proactive-alerts pipeline during the test.
    monkeypatch.setattr(
        "openexecutive.alerts.pipeline.schedule_evaluation",
        lambda *a, **k: None,
    )

    app = FastAPI()
    app.include_router(documents.router)
    app.state.store = _CapturingStore()
    return TestClient(app)


def _upload(
    client: TestClient,
    *,
    filename: str = "plan.md",
    content: bytes = b"# Plan\nGrow revenue 30%.",
    **data: str,
) -> Any:
    files = {"file": (filename, io.BytesIO(content), "text/markdown")}
    return client.post("/documents", files=files, data=data)


def test_domain_from_form_field_is_honored(client: TestClient) -> None:
    resp = _upload(client, domain="finance")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["domain"] == "finance"
    assert body["chunks_indexed"] >= 1

    store: _CapturingStore = client.app.state.store  # type: ignore[attr-defined]
    assert store.added, "expected at least one indexed chunk"
    assert all(m["domain"] == "finance" for m in store.added)


def test_domain_defaults_to_general_when_omitted(client: TestClient) -> None:
    resp = _upload(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["domain"] == "general"
    store: _CapturingStore = client.app.state.store  # type: ignore[attr-defined]
    assert all(m["domain"] == "general" for m in store.added)


def test_get_document_returns_extracted_text(client: TestClient) -> None:
    # Upload writes the original file to disk; the viewer reads it back.
    assert _upload(client).status_code == 200

    resp = client.get("/documents/plan.md")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == "plan.md"
    assert "Grow revenue 30%." in body["content"]


def test_indexed_chunk_metadata_uses_real_filename_not_temp_path(
    client: TestClient,
) -> None:
    """Regression test for issue #22: chunk metadata must point at the
    real uploaded filename, not the throwaway tempfile.NamedTemporaryFile
    path ingest_file reads from (which is deleted before this handler even
    returns)."""
    assert _upload(client).status_code == 200

    store: _CapturingStore = client.app.state.store  # type: ignore[attr-defined]
    assert store.added, "expected at least one indexed chunk"
    for meta in store.added:
        assert meta["filename"] == "plan.md"
        assert meta["source"] == "plan.md"
        assert "tmp" not in meta["source"].lower()


def test_reuploading_same_file_overwrites_via_explicit_delete_not_stable_ids(
    client: TestClient,
) -> None:
    """issue #22: chunk ids are derived ONLY from the unique per-request
    temp path — never from the uploaded filename. An earlier fix attempt
    got re-upload-overwrites by deriving chunk ids from display_name
    instead, but adversarial security review found that made display_name
    (attacker-influenced on some upload paths) a shared collision key: two
    unrelated uploads sharing a filename could upsert over each other's
    chunks and destroy content across trust boundaries. This route gets
    overwrite-on-reupload back a different way instead: an explicit
    delete-by-filename immediately before ingest (safe here specifically
    because this route sits behind the app-wide shared-secret auth gate,
    unlike the attachment ingest path) rather than relying on colliding
    chunk ids. This test confirms both halves: the ids themselves never
    collide (proving the risky mechanism didn't come back), and the live
    store still ends up holding only the latest upload's chunks (proving
    the explicit delete is doing the overwrite job instead)."""
    assert _upload(client).status_code == 200
    store: _CapturingStore = client.app.state.store  # type: ignore[attr-defined]
    first_ids = sorted(store.added_ids)
    assert first_ids, "expected at least one indexed chunk"

    store.added_ids.clear()
    store.added.clear()
    assert _upload(client).status_code == 200
    second_ids = sorted(store.added_ids)

    assert set(second_ids).isdisjoint(first_ids), (
        "re-uploading the identical file produced the SAME chunk ids as "
        "the first upload -- that would mean chunk ids are keyed off "
        "filename/display_name again, reintroducing the cross-upload "
        "collision issue #22's final fix deliberately removed"
    )
    assert set(store.documents.keys()) == set(second_ids), (
        "expected the route's explicit delete-before-ingest to leave only "
        "the second upload's chunks live -- the first upload's chunks "
        "should have been cleared, not left to accumulate alongside the "
        "new set"
    )


def test_get_document_missing_returns_404(client: TestClient) -> None:
    resp = client.get("/documents/does_not_exist.md")
    assert resp.status_code == 404


def test_get_document_rejects_dotfile(client: TestClient) -> None:
    # The filename guard rejects dotfiles / non-bare names so a crafted path
    # can't escape the docs directory. (URL-encoded `../` is additionally
    # collapsed by path normalization before it ever reaches the handler.)
    resp = client.get("/documents/.env")
    assert resp.status_code == 400
