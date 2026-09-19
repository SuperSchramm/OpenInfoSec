"""Regression tests for issue #36: documents authored or edited through the API
are written to a writable overlay on the persistent volume, not into the package
directory (the container image, replaced on every deploy).

Before, POST/PUT /knowledge/builtin wrote the markdown into the package and the
rows into the vector store. After a redeploy the file was gone but the rows
stayed, so the doc could not be listed, read, re-chunked or edited.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openexecutive.knowledge import loader as loader_mod
from openexecutive.knowledge.loader import (
    builtin_overlay_root,
    chunk_text,
    seed_builtin_knowledge,
    seed_failures,
)
from openexecutive.knowledge.store import ChromaDBStore

WORDS = " ".join(f"w{i}" for i in range(300))
EDITED = " ".join(f"e{i}" for i in range(300))


class _FakeReviewStore:
    def register(self, **_kw: Any) -> None: ...
    def touch_modified(self, *_a: Any) -> None: ...
    def delete_item(self, *_a: Any) -> None: ...


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from openexecutive.api.main import create_app

    shipped = tmp_path / "image" / "builtin"
    (shipped / "strategy").mkdir(parents=True)
    (shipped / "failures" / "strategy").mkdir(parents=True)
    (shipped / "strategy" / "shipped_doc.md").write_text(WORDS, encoding="utf-8")
    (shipped / "failures" / "strategy" / "shipped_fail.md").write_text(WORDS, encoding="utf-8")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", shipped)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", shipped / "failures")
    monkeypatch.setattr("openexecutive.api.routes.knowledge.BUILTIN_KNOWLEDGE_PATH", shipped)
    monkeypatch.setattr("openexecutive.api.routes.knowledge.FAILURES_KNOWLEDGE_PATH", shipped / "failures")
    monkeypatch.setattr("openexecutive.api.routes.knowledge._get_store", lambda _request: store)
    monkeypatch.setattr("openexecutive.knowledge.review_store.ReviewStore", _FakeReviewStore)
    asyncio.run(seed_builtin_knowledge(store=store))
    asyncio.run(seed_failures(store=store))
    return {"client": TestClient(create_app()), "store": store, "shipped": shipped, "overlay": builtin_overlay_root()}


def _sources(store: ChromaDBStore, collection: str) -> set[str]:
    got = store._get_or_create_collection(collection).get(include=["metadatas"])
    return {m["source"] for m in got["metadatas"]}


def _n(store: ChromaDBStore, collection: str, source: Path) -> int:
    return len(store._get_or_create_collection(collection).get(where={"source": str(source)}, include=[])["ids"])


def test_the_overlay_lives_outside_the_package_and_is_isolated_in_tests(env: dict[str, Any]) -> None:
    overlay = env["overlay"]
    assert not str(overlay).startswith(str(Path(loader_mod.__file__).parent))
    assert "builtin_overlay" in str(overlay)  # conftest's per-test temp dir, not the developer's real folder


def test_a_new_doc_is_written_to_the_overlay_never_to_the_package(env: dict[str, Any]) -> None:
    client, store, shipped, overlay = env["client"], env["store"], env["shipped"], env["overlay"]

    res = client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "new_doc.md", "content": WORDS})

    assert res.status_code == 200, res.text
    assert (overlay / "strategy" / "new_doc.md").read_text(encoding="utf-8") == WORDS
    assert not (shipped / "strategy" / "new_doc.md").exists(), "must not write into the package"
    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, overlay / "strategy" / "new_doc.md") == res.json()["chunks_indexed"] > 0
    files = {(f["filename"], f["origin"]) for f in client.get("/knowledge/builtin").json()["files"]}
    assert {("new_doc.md", "custom"), ("shipped_doc.md", "shipped")} <= files
    assert client.get("/knowledge/builtin/strategy/new_doc.md").json()["content"] == WORDS


def test_creating_a_doc_that_already_exists_in_either_place_is_a_conflict(env: dict[str, Any]) -> None:
    client = env["client"]
    assert client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "shipped_doc.md", "content": "x"}).status_code == 409
    client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "mine.md", "content": WORDS})
    assert client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "mine.md", "content": "x"}).status_code == 409


def test_editing_a_shipped_doc_saves_an_overlay_copy_and_leaves_the_shipped_file_alone(env: dict[str, Any]) -> None:
    client, store, shipped, overlay = env["client"], env["store"], env["shipped"], env["overlay"]
    shipped_file, custom = shipped / "strategy" / "shipped_doc.md", overlay / "strategy" / "shipped_doc.md"
    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, shipped_file) > 0

    res = client.put("/knowledge/builtin/strategy/shipped_doc.md", json={"domain": "strategy", "filename": "shipped_doc.md", "content": EDITED})

    assert res.status_code == 200, res.text
    assert shipped_file.read_text(encoding="utf-8") == WORDS, "the shipped file is untouched"
    assert custom.read_text(encoding="utf-8") == EDITED
    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, shipped_file) == 0, "the shipped doc's rows are replaced"
    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, custom) > 0
    assert client.get("/knowledge/builtin/strategy/shipped_doc.md").json()["content"] == EDITED
    listed = [f for f in client.get("/knowledge/builtin").json()["files"] if f["filename"] == "shipped_doc.md"]
    assert len(listed) == 1 and listed[0]["origin"] == "edited"


def test_deleting_a_shipped_doc_leaves_the_shipped_file_alone_and_records_a_tombstone(env: dict[str, Any]) -> None:
    client, store, shipped, overlay = env["client"], env["store"], env["shipped"], env["overlay"]
    client.put("/knowledge/builtin/strategy/shipped_doc.md", json={"domain": "strategy", "filename": "shipped_doc.md", "content": EDITED})

    assert client.delete("/knowledge/builtin/strategy/shipped_doc.md").status_code == 200

    assert (shipped / "strategy" / "shipped_doc.md").read_text(encoding="utf-8") == WORDS, "shipped files are never modified"
    assert not (overlay / "strategy" / "shipped_doc.md").exists()
    assert (overlay / ".deleted" / "strategy" / "shipped_doc.md.deleted").exists()
    assert not any("shipped_doc.md" in s for s in _sources(store, ChromaDBStore.BUILTIN_COLLECTION))
    assert client.get("/knowledge/builtin/strategy/shipped_doc.md").status_code == 404
    assert "shipped_doc.md" not in {f["filename"] for f in client.get("/knowledge/builtin").json()["files"]}
    assert client.delete("/knowledge/builtin/strategy/shipped_doc.md").status_code == 404


def test_a_deleted_shipped_doc_stays_deleted_across_a_redeploy_and_can_be_recreated(env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = env["client"], env["store"]
    client.delete("/knowledge/builtin/strategy/shipped_doc.md")
    count = store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION)

    new_image = tmp_path / "image2" / "builtin"  # the shipped file comes back with the new image
    (new_image / "strategy").mkdir(parents=True)
    (new_image / "strategy" / "shipped_doc.md").write_text(WORDS, encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", new_image)
    monkeypatch.setattr("openexecutive.api.routes.knowledge.BUILTIN_KNOWLEDGE_PATH", new_image)

    asyncio.run(seed_builtin_knowledge(store=store))
    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == count, "not re-indexed"
    assert "shipped_doc.md" not in {f["filename"] for f in client.get("/knowledge/builtin").json()["files"]}, "and not listed either"
    res = client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "shipped_doc.md", "content": EDITED})
    assert res.status_code == 200, res.text
    assert {f["filename"]: f["origin"] for f in client.get("/knowledge/builtin").json()["files"]}["shipped_doc.md"] == "edited"


def test_deleting_an_overlay_only_doc_removes_it_without_a_tombstone(env: dict[str, Any]) -> None:
    client, store, overlay = env["client"], env["store"], env["overlay"]
    client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "mine.md", "content": WORDS})
    assert client.delete("/knowledge/builtin/strategy/mine.md").status_code == 200
    assert not (overlay / "strategy" / "mine.md").exists()
    assert not (overlay / ".deleted" / "strategy" / "mine.md.deleted").exists()
    assert not any("mine.md" in s for s in _sources(store, ChromaDBStore.BUILTIN_COLLECTION))


def test_recreating_a_doc_whose_rows_were_orphaned_before_the_overlay_existed_does_not_duplicate_them(env: dict[str, Any]) -> None:
    """The bug this fixes left rows under the old package path with the file gone."""
    client, store, shipped = env["client"], env["store"], env["shipped"]
    old = shipped / "strategy" / "legacy.md"  # never existed on disk: rows only
    store.add_documents(
        texts=["old content"], metadatas=[{"domain": "strategy", "filename": "legacy.md", "source": str(old), "chunk_index": 0, "type": "builtin"}],
        ids=["legacy-0"], collection=ChromaDBStore.BUILTIN_COLLECTION,
    )

    assert client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "legacy.md", "content": WORDS}).status_code == 200

    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, old) == 0, "the stale rows are dropped"


def test_a_symlink_in_the_overlay_is_never_followed_out_of_it(env: dict[str, Any], tmp_path: Path) -> None:
    client, overlay = env["client"], env["overlay"]
    secret = tmp_path / "secret.md"
    secret.write_text("do not leak", encoding="utf-8")
    (overlay / "strategy").mkdir(parents=True)
    (overlay / "strategy" / "link.md").symlink_to(secret)

    assert client.get("/knowledge/builtin/strategy/link.md").status_code == 400
    assert client.put("/knowledge/builtin/strategy/link.md", json={"domain": "strategy", "filename": "link.md", "content": "x"}).status_code == 400
    assert secret.read_text(encoding="utf-8") == "do not leak"


def test_overlay_docs_are_registered_for_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.knowledge.review_store import ReviewStore

    shipped = tmp_path / "image" / "builtin"
    (shipped / "strategy").mkdir(parents=True)
    (shipped / "strategy" / "a.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", shipped)
    overlay = builtin_overlay_root()
    (overlay / "finance").mkdir(parents=True)
    (overlay / "finance" / "mine.md").write_text("x", encoding="utf-8")
    db = tmp_path / "review.db"
    ReviewStore.initialize_db(db_path=db)

    assert ReviewStore.sync_builtin_registrations(db_path=db) == 2


def test_an_overlay_doc_survives_a_redeploy_that_replaces_the_package(env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug itself: after the image is replaced the API-authored doc must still
    be there -- listed, readable, indexed exactly once, and editable."""
    client, store = env["client"], env["store"]
    client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "mine.md", "content": WORDS})
    client.put("/knowledge/builtin/strategy/shipped_doc.md", json={"domain": "strategy", "filename": "shipped_doc.md", "content": EDITED})
    count = store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION)

    new_image = tmp_path / "image2" / "builtin"  # a fresh image: shipped files only, none of the API-authored ones
    (new_image / "strategy").mkdir(parents=True)
    (new_image / "strategy" / "shipped_doc.md").write_text(WORDS, encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", new_image)
    monkeypatch.setattr("openexecutive.api.routes.knowledge.BUILTIN_KNOWLEDGE_PATH", new_image)
    asyncio.run(seed_builtin_knowledge(store=store))

    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == count, "nothing duplicated or lost"
    overlay = env["overlay"]
    for name in ("mine.md", "shipped_doc.md"):  # each doc is indexed from exactly one place, once
        assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, overlay / "strategy" / name) == len(chunk_text(EDITED if name == "shipped_doc.md" else WORDS, 120, 20))
    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, env["shipped"] / "strategy" / "shipped_doc.md") == 0
    files = {f["filename"]: f["origin"] for f in client.get("/knowledge/builtin").json()["files"]}
    assert files["mine.md"] == "custom" and files["shipped_doc.md"] == "edited"
    assert client.get("/knowledge/builtin/strategy/mine.md").json()["content"] == WORDS
    assert client.put("/knowledge/builtin/strategy/mine.md", json={"domain": "strategy", "filename": "mine.md", "content": EDITED}).status_code == 200


def test_seeding_indexes_overlay_docs_with_the_domain_of_their_folder(env: dict[str, Any], tmp_path: Path) -> None:
    overlay = env["overlay"]
    (overlay / "finance").mkdir(parents=True)
    (overlay / "finance" / "budget.md").write_text(WORDS, encoding="utf-8")
    fresh = ChromaDBStore(persist_directory=tmp_path / "fresh-chroma")

    asyncio.run(seed_builtin_knowledge(store=fresh))

    got = fresh._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(include=["metadatas"])
    by_file = {m["filename"]: m["domain"] for m in got["metadatas"]}
    assert by_file["budget.md"] == "finance" and by_file["shipped_doc.md"] == "strategy"
    assert "shipped_fail.md" not in by_file, "failure cases are their own corpus"


def test_an_overlay_file_shadows_the_shipped_one_at_seed_time(env: dict[str, Any], tmp_path: Path) -> None:
    overlay, shipped = env["overlay"], env["shipped"]
    (overlay / "strategy").mkdir(parents=True)
    (overlay / "strategy" / "shipped_doc.md").write_text(EDITED, encoding="utf-8")
    store = env["store"]

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    sources = _sources(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert str(overlay / "strategy" / "shipped_doc.md") in sources
    assert str(shipped / "strategy" / "shipped_doc.md") not in sources, "shadowed shipped rows are replaced, not duplicated"
    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == len(chunk_text(EDITED, 120, 20))


def test_failure_cases_use_the_overlay_the_same_way(env: dict[str, Any]) -> None:
    client, store, shipped, overlay = env["client"], env["store"], env["shipped"], env["overlay"]

    res = client.post("/knowledge/failures", json={"domain": "strategy", "filename": "my_fail.md", "content": WORDS})
    assert res.status_code == 200, res.text
    assert (overlay / "failures" / "strategy" / "my_fail.md").exists()
    assert not (shipped / "failures" / "strategy" / "my_fail.md").exists()

    res = client.put("/knowledge/failures/strategy/shipped_fail.md", json={"domain": "strategy", "filename": "shipped_fail.md", "content": EDITED})
    assert res.status_code == 200, res.text
    assert (shipped / "failures" / "strategy" / "shipped_fail.md").read_text(encoding="utf-8") == WORDS
    assert _n(store, ChromaDBStore.FAILURES_COLLECTION, shipped / "failures" / "strategy" / "shipped_fail.md") == 0
    listed = {f["filename"]: f["origin"] for f in client.get("/knowledge/failures").json()["files"]}
    assert listed == {"my_fail.md": "custom", "shipped_fail.md": "edited"}

    assert client.delete("/knowledge/failures/strategy/my_fail.md").status_code == 200
    assert not (overlay / "failures" / "strategy" / "my_fail.md").exists()


def test_review_registration_ignores_a_skills_folder_above_the_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same bug class as #35: judged on the absolute path, a checkout under a
    folder named "skills" registered nothing."""
    from openexecutive.knowledge.review_store import ReviewStore

    corpus = tmp_path / "skills" / "checkout" / "builtin"
    (corpus / "strategy").mkdir(parents=True)
    (corpus / "strategy" / "a.md").write_text("x", encoding="utf-8")
    (corpus / "skills").mkdir()
    (corpus / "skills" / "s.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", corpus)
    db = tmp_path / "review.db"
    ReviewStore.initialize_db(db_path=db)

    assert ReviewStore.sync_builtin_registrations(db_path=db) == 1  # a.md; the real skills/ folder is skipped


def test_a_hand_copied_overlay_file_replaces_the_current_shipped_rows_on_a_normal_boot(env: dict[str, Any]) -> None:
    """No PUT ran, so nothing dropped the shipped rows; a normal (non-force) boot must."""
    store, overlay, shipped = env["store"], env["overlay"], env["shipped"]
    (overlay / "strategy").mkdir(parents=True)
    (overlay / "strategy" / "shipped_doc.md").write_text(EDITED, encoding="utf-8")

    asyncio.run(seed_builtin_knowledge(store=store))  # not forced

    sources = _sources(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert str(overlay / "strategy" / "shipped_doc.md") in sources
    assert str(shipped / "strategy" / "shipped_doc.md") not in sources, "both versions must not be retrievable at once"
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0, "and it settles"


def test_a_failed_overlay_write_on_an_edit_leaves_the_doc_indexed(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A full or read-only volume: the PUT fails, and the shipped doc keeps its rows."""
    client, store, shipped = env["client"], env["store"], env["shipped"]
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("BUILTIN_OVERLAY_PATH", str(blocker))
    before = _n(store, ChromaDBStore.BUILTIN_COLLECTION, shipped / "strategy" / "shipped_doc.md")
    assert before > 0

    res = client.put("/knowledge/builtin/strategy/shipped_doc.md", json={"domain": "strategy", "filename": "shipped_doc.md", "content": EDITED})
    assert res.status_code == 500, "an unwritable volume is an operational failure, not a security refusal"

    assert _n(store, ChromaDBStore.BUILTIN_COLLECTION, shipped / "strategy" / "shipped_doc.md") == before


def test_overlay_failure_cases_are_seeded_even_when_the_shipped_failures_dir_is_missing(env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = env["overlay"]
    (overlay / "failures" / "strategy").mkdir(parents=True)
    (overlay / "failures" / "strategy" / "mine.md").write_text(WORDS, encoding="utf-8")
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", tmp_path / "no-such-dir")
    fresh = ChromaDBStore(persist_directory=tmp_path / "fresh")

    assert asyncio.run(seed_failures(store=fresh)) == len(chunk_text(WORDS, 120, 20))


# ---- round 2 review

def test_a_symlinked_failures_folder_cannot_lead_the_failures_routes_out_of_the_overlay(env: dict[str, Any], tmp_path: Path) -> None:
    client, overlay = env["client"], env["overlay"]
    outside = tmp_path / "outside"
    outside.mkdir()
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "failures").symlink_to(outside)

    res = client.post("/knowledge/failures", json={"domain": "legal", "filename": "evil.md", "content": WORDS})

    assert res.status_code == 400
    assert not list(outside.rglob("*")), "nothing written outside the overlay"


def test_one_unsafe_overlay_entry_does_not_break_the_listing_or_the_seed(env: dict[str, Any], tmp_path: Path) -> None:
    client, overlay, store = env["client"], env["overlay"], env["store"]
    secret = tmp_path / "secret.md"
    secret.write_text("do not index " * 50, encoding="utf-8")
    (overlay / "strategy").mkdir(parents=True)
    (overlay / "strategy" / "link.md").symlink_to(secret)
    client.post("/knowledge/builtin", json={"domain": "strategy", "filename": "fine.md", "content": WORDS})

    res = client.get("/knowledge/builtin")
    assert res.status_code == 200
    names = {f["filename"] for f in res.json()["files"]}
    assert "fine.md" in names and "link.md" not in names

    asyncio.run(seed_builtin_knowledge(store=store, force=True))
    assert not any(str(secret) in s or "link.md" in s for s in _sources(store, ChromaDBStore.BUILTIN_COLLECTION))


def test_a_deleted_shipped_doc_is_not_re_registered_for_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.knowledge.loader import tombstone_path
    from openexecutive.knowledge.review_store import ReviewStore

    shipped = tmp_path / "image" / "builtin"
    (shipped / "strategy").mkdir(parents=True)
    (shipped / "strategy" / "a.md").write_text("x", encoding="utf-8")
    (shipped / "strategy" / "b.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", shipped)
    tomb = tombstone_path(builtin_overlay_root(), Path("strategy") / "a.md")
    tomb.parent.mkdir(parents=True)
    tomb.touch()
    db = tmp_path / "review.db"
    ReviewStore.initialize_db(db_path=db)

    assert ReviewStore.sync_builtin_registrations(db_path=db) == 1  # b.md only


def test_a_relative_overlay_setting_is_resolved_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUILTIN_OVERLAY_PATH", "./builtin_custom")
    assert builtin_overlay_root() == (tmp_path / "builtin_custom").resolve()
    assert builtin_overlay_root().is_absolute()


def test_editing_a_shipped_doc_after_the_install_moved_replaces_the_old_install_rows(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shipped rows stored under a previous install prefix must not survive the overlay copy."""
    client, store, shipped = env["client"], env["store"], env["shipped"]
    moved = tmp_path / "moved-image" / "builtin"
    moved.parent.mkdir()
    shipped.rename(moved)
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", moved)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", moved / "failures")
    monkeypatch.setattr("openexecutive.api.routes.knowledge.BUILTIN_KNOWLEDGE_PATH", moved)
    monkeypatch.setattr("openexecutive.api.routes.knowledge.FAILURES_KNOWLEDGE_PATH", moved / "failures")
    (env["overlay"] / "strategy").mkdir(parents=True)
    (env["overlay"] / "strategy" / "shipped_doc.md").write_text(EDITED, encoding="utf-8")  # the edit exists on the volume

    asyncio.run(seed_builtin_knowledge(store=store))  # normal boot on the moved install

    sources = _sources(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert not any(src.endswith("image/builtin/strategy/shipped_doc.md") for src in sources), "old-install rows are replaced"
    assert str(env["overlay"] / "strategy" / "shipped_doc.md") in sources
