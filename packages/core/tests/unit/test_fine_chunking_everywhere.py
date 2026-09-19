"""Regression tests for issue #32: built-in, failure-case, Notion and research
text use the same embedding-window-sized chunking as company docs (issue #31),
and a store seeded before this change is re-chunked exactly once.

Chroma's default embedding model embeds only the first 256 tokens of its input.
The built-in library was chunked at 512 words (median ~750 tokens; 199 of 218
chunks over the window) and failure cases at 400 (41 of 45 over), so most of
every chunk was unsearchable. Built-in seeding only ran on an EMPTY collection,
so an existing install would never have picked up new chunking without the
migration these tests pin.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openexecutive.knowledge import loader as loader_mod
from openexecutive.knowledge.loader import (
    CHUNKING_VERSION,
    FINE_CHUNK_MAX_WORDS,
    FINE_CHUNK_OVERLAP,
    FINE_CHUNK_WORDS,
    LEGACY_CHUNK_OVERLAP,
    LEGACY_CHUNK_WORDS,
    chunk_text,
    default_chunking,
    ingest_builtin_file,
    ingest_text_sync,
    seed_builtin_knowledge,
    seed_failures,
)
from openexecutive.knowledge.store import ChromaDBStore


def _words(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


# ---------------------------------------------------------------- default_chunking

def test_default_chunking_is_the_fine_profile_for_normal_text() -> None:
    assert default_chunking(_words(5000)) == (FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)
    assert default_chunking("") == (FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)


def test_default_chunking_falls_back_to_legacy_past_the_word_limit() -> None:
    assert default_chunking(_words(FINE_CHUNK_MAX_WORDS)) == (FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)
    assert default_chunking(_words(FINE_CHUNK_MAX_WORDS + 1)) == (LEGACY_CHUNK_WORDS, LEGACY_CHUNK_OVERLAP)


def test_default_chunking_counts_words_not_characters() -> None:
    # Lots of characters but few words (long tokens): must NOT trip the limit.
    long_tokens = " ".join(["x" * 40] * 20_000)  # 820k chars, 20k words
    assert len(long_tokens) > 2 * FINE_CHUNK_MAX_WORDS
    assert default_chunking(long_tokens) == (FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)
    # ...and the densest possible text (1-char words) over the limit must.
    dense = " ".join(["a"] * (FINE_CHUNK_MAX_WORDS + 1))
    assert default_chunking(dense) == (LEGACY_CHUNK_WORDS, LEGACY_CHUNK_OVERLAP)


def test_the_chunking_version_tracks_the_sizes() -> None:
    assert f"fine-{FINE_CHUNK_WORDS}-{FINE_CHUNK_OVERLAP}" == CHUNKING_VERSION


# --------------------------------------------------- real corpus fits the window

def _tokenizer_json() -> Path | None:
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    except Exception:
        return None
    hits = list(Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH).rglob("tokenizer.json"))
    return hits[0] if hits else None


@pytest.mark.skipif(_tokenizer_json() is None, reason="embedding model tokenizer not cached")
def test_every_real_builtin_and_failure_chunk_fits_the_embedding_window() -> None:
    """Over the ACTUAL shipped corpus, not a synthetic sample: markdown tables and
    bullets tokenize denser than prose, and this is what guards a future edit to
    the sizes (or a dense new doc) against silently pushing chunks back over."""
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(_tokenizer_json()))
    tok.no_truncation()
    tok.no_padding()
    files = [
        p for p in loader_mod.BUILTIN_KNOWLEDGE_PATH.rglob("*.md")
        if "skills" not in p.relative_to(loader_mod.BUILTIN_KNOWLEDGE_PATH).parts
    ]
    assert len(files) > 50, "expected the shipped built-in corpus"
    worst = 0
    worst_old = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        worst = max(worst, max((len(tok.encode(c).ids) for c in chunk_text(text, *default_chunking(text))), default=0))
        worst_old = max(worst_old, max((len(tok.encode(c).ids) for c in chunk_text(text)), default=0))
    assert worst <= 256, f"a built-in/failure chunk exceeds the 256-token window: {worst}"
    assert worst_old > 256  # control: the old default really overflowed, so this isn't vacuous


# ------------------------------------------------------------ ingest_* defaults

def test_ingest_text_sync_uses_the_fine_profile_for_notion_and_research(tmp_path: Path) -> None:
    class _Store:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def add_documents(self, texts, metadatas, ids, collection):  # noqa: ANN001
            self.texts.extend(texts)

    store = _Store()
    n = ingest_text_sync(_words(1000), store, source_name="page", collection="notion_wiki")  # type: ignore[arg-type]
    assert n == len(store.texts) > 5
    assert store.texts == chunk_text(_words(1000), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)


def test_ingest_builtin_file_defaults_to_fine_chunks_with_the_version_marker(tmp_path: Path) -> None:
    path = tmp_path / "strategy" / "playbook.md"
    path.parent.mkdir()
    path.write_text(_words(500), encoding="utf-8")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")

    n = asyncio.run(ingest_builtin_file(path, store))

    assert n == len(chunk_text(_words(500), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)) > 3
    assert _ids(store, ChromaDBStore.BUILTIN_COLLECTION) and _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}


def test_ingest_builtin_file_explicit_sizes_win_and_must_come_together(tmp_path: Path) -> None:
    path = tmp_path / "strategy" / "playbook.md"
    path.parent.mkdir()
    path.write_text(_words(500), encoding="utf-8")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")

    assert asyncio.run(ingest_builtin_file(path, store, chunk_size=250, overlap=25)) == len(chunk_text(_words(500), 250, 25))
    with pytest.raises(ValueError, match="together"):
        asyncio.run(ingest_builtin_file(path, store, chunk_size=250))


# ------------------------------------------------------------------ migration

@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    builtin = tmp_path / "builtin"
    (builtin / "strategy").mkdir(parents=True)
    (builtin / "finance").mkdir()
    (builtin / "strategy" / "alpha.md").write_text(_words(420, "alpha"), encoding="utf-8")
    (builtin / "finance" / "beta.md").write_text(_words(260, "beta"), encoding="utf-8")
    failures = builtin / "failures"
    (failures / "strategy").mkdir(parents=True)
    (failures / "strategy" / "kodak.md").write_text(_words(330, "kodak"), encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", builtin)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", failures)
    return {
        "alpha": builtin / "strategy" / "alpha.md",
        "beta": builtin / "finance" / "beta.md",
        "kodak": failures / "strategy" / "kodak.md",
        "store": ChromaDBStore(persist_directory=tmp_path / "chroma"),
        "alpha_n": len(chunk_text(_words(420, "alpha"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)),
        "beta_n": len(chunk_text(_words(260, "beta"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)),
        "kodak_n": len(chunk_text(_words(330, "kodak"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)),
    }


def _seed_old_style(
    store: ChromaDBStore, collection: str, chunk_type: str, source: Path, extra: dict[str, Any] | None = None,
    tag: str = "",
) -> list[str]:
    """What an install seeded before #32 looks like: big chunks, no marker."""
    ids = [f"old-{chunk_type}{tag}-{i}" for i in (0, 1)]
    store.add_documents(
        texts=["old big chunk one", "old big chunk two"],
        metadatas=[
            {"domain": "strategy", "filename": source.name, "source": str(source), "chunk_index": i,
             "type": chunk_type, **(extra or {})}
            for i in (0, 1)
        ],
        ids=ids,
        collection=collection,
    )
    return ids


def _stale(store: ChromaDBStore, collection: str, chunk_type: str) -> dict[str, str | None]:
    return store.stale_chunk_sources(collection, {"type": chunk_type}, CHUNKING_VERSION)


def _ids(store: ChromaDBStore, collection: str) -> set[str]:
    return set(store._get_or_create_collection(collection).get(include=[])["ids"])


def test_a_fresh_store_is_seeded_with_fine_chunks_and_the_marker(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    total = asyncio.run(seed_builtin_knowledge(store=store))
    assert total == corpus["alpha_n"] + corpus["beta_n"]
    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == total
    assert _ids(store, ChromaDBStore.BUILTIN_COLLECTION) and _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}


def test_a_current_store_is_left_alone(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0


def test_a_pre_issue_32_store_is_re_chunked_once_and_external_rows_survive(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])
    store.add_documents(
        texts=["external OER textbook chunk"],
        metadatas=[{"domain": "strategy", "filename": "oer.md", "type": "external", "source_id": "oer-1", "chunk_index": 0}],
        ids=["ext-1"],
        collection=ChromaDBStore.BUILTIN_COLLECTION,
    )
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin")

    total = asyncio.run(seed_builtin_knowledge(store=store))

    assert total == corpus["alpha_n"] + corpus["beta_n"]
    col = store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION)
    got = col.get(include=["metadatas"])
    assert "old-builtin-0" not in got["ids"] and "old-builtin-1" not in got["ids"], "stale big chunks must be gone"
    assert "ext-1" in got["ids"], "external OER rows share this collection and must be left alone"
    assert _ids(store, ChromaDBStore.BUILTIN_COLLECTION) and _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0, "migration must be one-time"


def test_a_store_marked_with_a_different_profile_is_migrated(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"], {"chunking": "fine-200-30"})
    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0
    assert _ids(store, ChromaDBStore.BUILTIN_COLLECTION) and _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}


def test_a_collection_holding_only_external_rows_still_gets_the_shipped_docs(corpus: dict[str, Any]) -> None:
    """External OER rows share this collection. "No built-in rows" is not
    "stale" (nothing is deleted), but it does mean the shipped docs are new."""
    store = corpus["store"]
    store.add_documents(
        texts=["external"], metadatas=[{"domain": "strategy", "type": "external", "source_id": "s", "chunk_index": 0}],
        ids=["ext-only"], collection=ChromaDBStore.BUILTIN_COLLECTION,
    )
    assert asyncio.run(seed_builtin_knowledge(store=store)) == corpus["alpha_n"] + corpus["beta_n"]
    assert "ext-only" in _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_force_reseed_still_works(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    assert asyncio.run(seed_builtin_knowledge(store=store, force=True)) == corpus["alpha_n"] + corpus["beta_n"]


def test_failure_cases_are_seeded_and_migrated_the_same_way(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    _seed_old_style(store, ChromaDBStore.FAILURES_COLLECTION, "failure_case", corpus["kodak"])
    assert _stale(store, ChromaDBStore.FAILURES_COLLECTION, "failure_case")

    assert asyncio.run(seed_failures(store=store)) == corpus["kodak_n"]

    got = store._get_or_create_collection(ChromaDBStore.FAILURES_COLLECTION).get(include=["metadatas"])
    assert all(i.startswith("old-") is False for i in got["ids"])
    assert _ids(store, ChromaDBStore.FAILURES_COLLECTION) and _stale(store, ChromaDBStore.FAILURES_COLLECTION, "failure_case") == {}
    assert asyncio.run(seed_failures(store=store)) == 0


# ---- crash safety / rows the migration must not touch (adversarial review, issue #32)

def test_a_crash_mid_migration_loses_nothing_and_the_next_start_finishes(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upsert-then-delete: kill the process after the first file is written and
    the store must still hold every old row plus the new ones, still be
    reported stale, and be completed by the next startup."""
    store = corpus["store"]
    old = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])
    real_add = store.add_documents
    calls = {"n": 0}

    def flaky(*a: Any, **kw: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("embedding model unavailable")
        real_add(*a, **kw)

    monkeypatch.setattr(store, "add_documents", flaky)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0  # logged, startup carries on

    assert set(old) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION), "old rows must survive an interrupted migration"
    monkeypatch.setattr(store, "add_documents", real_add)
    asyncio.run(seed_builtin_knowledge(store=store))  # redoes only what the crash left unfinished
    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == corpus["alpha_n"] + corpus["beta_n"]
    assert not set(old) & _ids(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0


def test_rows_whose_source_file_is_gone_are_kept_and_do_not_retrigger_the_migration(corpus: dict[str, Any], tmp_path: Path) -> None:
    """Docs added through POST /knowledge/builtin live on the (ephemeral) package
    disk but in the persistent store; after a redeploy the file is gone. They
    can't be re-chunked, so they must survive -- and must not make every later
    startup think a migration is pending."""
    store = corpus["store"]
    orphan = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "gone" / "api-authored.md", tag="-orphan")
    _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])

    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0
    assert set(orphan) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0


def test_orphans_alone_are_never_migrated_or_deleted(corpus: dict[str, Any], tmp_path: Path) -> None:
    store = corpus["store"]
    orphan = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "gone.md")
    asyncio.run(seed_builtin_knowledge(store=store))  # the shipped docs are new and get added
    assert set(orphan) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0


def test_a_missing_failures_directory_never_empties_the_collection(corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = corpus["store"]
    old = _seed_old_style(store, ChromaDBStore.FAILURES_COLLECTION, "failure_case", corpus["kodak"])
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", tmp_path / "nope")
    assert asyncio.run(seed_failures(store=store)) == 0
    assert set(old) == _ids(store, ChromaDBStore.FAILURES_COLLECTION)


def test_a_missing_builtin_directory_never_empties_the_collection(corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = corpus["store"]
    old = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", tmp_path / "nope")
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert set(old) == _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_an_undecodable_file_is_skipped_not_fatal(corpus: dict[str, Any]) -> None:
    (corpus["alpha"].parent / "latin1.md").write_bytes(b"caf\xe9 " * 200)
    total = asyncio.run(seed_builtin_knowledge(store=corpus["store"]))
    assert total == corpus["alpha_n"] + corpus["beta_n"]


def test_a_failed_leftover_delete_is_retried_on_the_next_start(corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    store = corpus["store"]
    old = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])
    real_delete = store.delete_ids
    monkeypatch.setattr(store, "delete_ids", lambda *a, **k: None)  # simulate a swallowed failure
    asyncio.run(seed_builtin_knowledge(store=store))
    assert set(old) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)

    monkeypatch.setattr(store, "delete_ids", real_delete)
    asyncio.run(seed_builtin_knowledge(store=store))
    assert not set(old) & _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_an_unreadable_file_keeps_its_old_rows(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    bad = corpus["alpha"].parent / "bad.md"
    bad.write_bytes(b"\xff\xfe\xfa words")
    old = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", bad, tag="-bad")
    _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])

    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0

    assert set(old) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION), "an unreadable file's rows must survive"
    assert set(_stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin")) == set(old), "...and stay stale, to be retried"


def test_a_crash_between_batches_of_one_big_file_is_still_stale(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_documents upserts 100 rows at a time. Real md5 ids, so the first
    batch overwrites old rows: it must not leave rows that read as current."""
    store = corpus["store"]
    big = corpus["alpha"].parent / "big.md"
    big.write_text(_words(15_000, "big"), encoding="utf-8")
    n = len(chunk_text(_words(15_000, "big"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert n > 100
    real_add = store.add_documents
    store.add_documents(
        texts=["old"] * 5,
        metadatas=[{"domain": "strategy", "filename": "big.md", "source": str(big), "chunk_index": i, "type": "builtin"} for i in range(5)],
        ids=[loader_mod._make_chunk_id(str(big), i) for i in range(5)],
        collection=ChromaDBStore.BUILTIN_COLLECTION,
    )

    def first_batch_only(texts: list[str], metadatas: list[dict[str, Any]], ids: list[str], collection: str) -> None:
        if len(texts) <= 100:  # the small files are single-batch: let them through
            real_add(texts, metadatas, ids, collection)
            return
        real_add(texts[:100], metadatas[:100], ids[:100], collection)
        raise RuntimeError("killed between batches")

    monkeypatch.setattr(store, "add_documents", first_batch_only)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0  # logged, startup carries on
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin"), "a truncated file must still read as stale"

    monkeypatch.setattr(store, "add_documents", real_add)
    asyncio.run(seed_builtin_knowledge(store=store))
    got = [i for i in _ids(store, ChromaDBStore.BUILTIN_COLLECTION) if i in {loader_mod._make_chunk_id(str(big), k) for k in range(n)}]
    assert len(got) == n
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}


def test_stale_chunk_sources_is_empty_for_a_missing_collection(tmp_path: Path) -> None:
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    assert store.stale_chunk_sources("no_such_collection", {"type": "builtin"}, CHUNKING_VERSION) == {}


# ------------------------------------------------- failure CRUD routes: lockstep

def test_failure_routes_chunk_exactly_like_seed_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes used to pass chunk_size=400/overlap=40 by hand to match
    seed_failures. Both now use the shared default, so a PUT/POST re-index must
    yield the same chunks and marker as seeding did."""
    from fastapi.testclient import TestClient

    from openexecutive.api.main import create_app

    failures = tmp_path / "failures"
    failures.mkdir()
    monkeypatch.setattr("openexecutive.api.routes.knowledge.FAILURES_KNOWLEDGE_PATH", failures)
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    monkeypatch.setattr("openexecutive.api.routes.knowledge._get_store", lambda _request: store)
    content = _words(400, "story")

    client = TestClient(create_app())
    res = client.post("/knowledge/failures", json={"domain": "strategy", "filename": "case.md", "content": content})

    assert res.status_code == 200, res.text
    expected = len(chunk_text(content, *default_chunking(content)))
    assert res.json()["chunks_indexed"] == expected > 3
    assert _ids(store, ChromaDBStore.FAILURES_COLLECTION) and _stale(store, ChromaDBStore.FAILURES_COLLECTION, "failure_case") == {}

    # PUT re-indexes the same way
    res = client.put("/knowledge/failures/strategy/case.md", json={"domain": "strategy", "filename": "case.md", "content": content})
    assert res.status_code == 200, res.text
    assert res.json()["chunks_indexed"] == expected


def test_force_reseed_drops_leftover_rows_of_a_file_that_shrank(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    before = store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION)
    corpus["alpha"].write_text(_words(100, "alpha"), encoding="utf-8")  # was 420 words

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    kept = len(chunk_text(_words(100, "alpha"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == kept + corpus["beta_n"] < before


def test_force_reseed_leaves_external_and_orphan_rows(corpus: dict[str, Any], tmp_path: Path) -> None:
    store = corpus["store"]
    orphan = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "gone.md", tag="-orphan")
    store.add_documents(
        texts=["ext"], metadatas=[{"domain": "strategy", "type": "external", "source_id": "s", "chunk_index": 0}],
        ids=["ext-force"], collection=ChromaDBStore.BUILTIN_COLLECTION,
    )
    asyncio.run(seed_builtin_knowledge(store=store, force=True))
    assert set(orphan) | {"ext-force"} <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


# ---- new shipped docs reach an already-seeded store (PCI DSS docs, #33-style follow-up)

def test_a_doc_added_in_a_later_release_is_indexed_into_an_existing_store(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    before = _ids(store, ChromaDBStore.BUILTIN_COLLECTION)
    new = corpus["alpha"].parent / "pci_new.md"
    new.write_text(_words(300, "pci"), encoding="utf-8")

    added = asyncio.run(seed_builtin_knowledge(store=store))

    assert added == len(chunk_text(_words(300, "pci"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)) > 2
    assert before < _ids(store, ChromaDBStore.BUILTIN_COLLECTION), "existing rows untouched, new ones added"
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0, "and only once"


def test_a_doc_the_store_already_has_is_not_duplicated_when_the_install_path_moves(
    corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    count = store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION)
    moved = tmp_path / "moved"
    (tmp_path / "builtin").rename(moved)
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", moved)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", moved / "failures")

    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == count


def test_an_unreadable_store_adds_nothing(corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    (store.persist_directory / f"seed_manifest_{ChromaDBStore.BUILTIN_COLLECTION}.json").unlink()  # force a bootstrap
    (corpus["alpha"].parent / "extra.md").write_text(_words(200, "x"), encoding="utf-8")
    monkeypatch.setattr(store, "all_chunk_sources", lambda *a, **k: None)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0


def test_new_failure_case_files_are_picked_up_too(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_failures(store=store))
    (corpus["kodak"].parent / "another.md").write_text(_words(240, "fail"), encoding="utf-8")
    assert asyncio.run(seed_failures(store=store)) == len(chunk_text(_words(240, "fail"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))


def test_all_chunk_sources_is_none_for_a_missing_collection(tmp_path: Path) -> None:
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    assert store.all_chunk_sources("no_such_collection", {"type": "builtin"}) is None


# ---- manifest: what a previous startup saw, not "has rows" (review of the new-file pickup)

def _rows_of(store: ChromaDBStore, source: Path) -> list[str]:
    got = store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(where={"source": str(source)}, include=[])
    return got["ids"]


def test_a_shipped_doc_an_admin_deleted_is_not_resurrected_by_a_restart(corpus: dict[str, Any]) -> None:
    """DELETE /knowledge/builtin/... removes the rows (and the file, which the
    image brings back on the next deploy). The manifest remembers the file was
    already handled, so its presence on disk must not re-add it."""
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    store.delete_documents(ChromaDBStore.BUILTIN_COLLECTION, where={"source": str(corpus["beta"])})
    assert not _rows_of(store, corpus["beta"])

    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert not _rows_of(store, corpus["beta"])


def test_same_filename_in_a_subdirectory_is_a_different_file(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    sub = corpus["alpha"].parent / "sub"
    sub.mkdir()
    (sub / "alpha.md").write_text(_words(200, "sub"), encoding="utf-8")

    assert asyncio.run(seed_builtin_knowledge(store=store)) == len(chunk_text(_words(200, "sub"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert _rows_of(store, sub / "alpha.md")


def test_first_start_after_upgrade_bootstraps_the_manifest_from_existing_rows(corpus: dict[str, Any]) -> None:
    """An install seeded before manifests existed: docs with rows are recorded as
    handled (no duplicates), a doc without rows is new, and the manifest is
    written so later deletions stick."""
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    manifest = store.persist_directory / f"seed_manifest_{ChromaDBStore.BUILTIN_COLLECTION}.json"
    manifest.unlink()
    count = store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION)
    (corpus["alpha"].parent / "pci_new.md").write_text(_words(200, "pci"), encoding="utf-8")

    added = asyncio.run(seed_builtin_knowledge(store=store))

    assert added == len(chunk_text(_words(200, "pci"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == count + added
    assert store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION) == {"strategy/alpha.md", "finance/beta.md", "strategy/pci_new.md"}


def test_a_failure_while_indexing_a_new_doc_does_not_break_startup_and_is_retried(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    (corpus["alpha"].parent / "later.md").write_text(_words(200, "later"), encoding="utf-8")
    real_add = store.add_documents

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr(store, "add_documents", boom)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0  # logged, not raised

    monkeypatch.setattr(store, "add_documents", real_add)
    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0
    assert "strategy/later.md" in store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION)


def test_an_unreadable_new_doc_is_retried_not_recorded_as_handled(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    bad = corpus["alpha"].parent / "bad.md"
    bad.write_bytes(b"\xff\xfe\xfa words")
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert "strategy/bad.md" not in store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION)

    bad.write_text(_words(200, "fixed"), encoding="utf-8")
    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0


def test_a_corrupt_manifest_is_treated_as_missing(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    (store.persist_directory / f"seed_manifest_{ChromaDBStore.BUILTIN_COLLECTION}.json").write_text("{not json", encoding="utf-8")
    assert store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION) is None
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0  # bootstraps from rows, adds nothing
    assert store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION) == {"strategy/alpha.md", "finance/beta.md"}


# ---- round 2 review: a bad mount or a failed index must not undo an admin's delete

def test_a_missing_corpus_root_does_not_wipe_the_manifest(corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    store.delete_documents(ChromaDBStore.BUILTIN_COLLECTION, where={"source": str(corpus["beta"])})  # admin deletes beta
    real_root = loader_mod.BUILTIN_KNOWLEDGE_PATH

    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", tmp_path / "not-mounted")  # bad mount: no files at all
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION) == {"strategy/alpha.md", "finance/beta.md"}

    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", real_root)  # mount comes back
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert not _rows_of(store, corpus["beta"])


def test_a_half_written_new_doc_is_retried_alone_and_does_not_resurrect_deleted_docs(
    corpus: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new doc fails between writing its rows and stamping them. The leftover
    pending rows make the next start see "stale rows with a source on disk";
    that must re-do just that file, not every shipped file."""
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    store.delete_documents(ChromaDBStore.BUILTIN_COLLECTION, where={"source": str(corpus["beta"])})
    new = corpus["alpha"].parent / "big.md"
    new.write_text(_words(300, "big"), encoding="utf-8")
    real_stamp = store.stamp_chunking

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("killed before stamping")

    monkeypatch.setattr(store, "stamp_chunking", boom)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert _rows_of(store, new), "pending rows were written"

    monkeypatch.setattr(store, "stamp_chunking", real_stamp)
    assert asyncio.run(seed_builtin_knowledge(store=store)) == len(chunk_text(_words(300, "big"), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert not _rows_of(store, corpus["beta"]), "the deleted doc must stay deleted"
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}


def test_re_chunking_a_legacy_store_skips_a_doc_the_manifest_says_was_deleted(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"])
    store.write_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION, {"strategy/alpha.md", "finance/beta.md"})  # beta was handled, then deleted

    assert asyncio.run(seed_builtin_knowledge(store=store)) == corpus["alpha_n"]
    assert not _rows_of(store, corpus["beta"])


def test_a_blank_shipped_file_is_indexed_once_it_gets_content(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    blank = corpus["alpha"].parent / "later.md"
    blank.write_text("   \n", encoding="utf-8")
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert "strategy/later.md" not in store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION)

    blank.write_text(_words(200, "later"), encoding="utf-8")
    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0


def test_a_partial_corpus_view_does_not_prune_the_manifest(corpus: dict[str, Any], tmp_path: Path) -> None:
    """Half the corpus is missing (a subdirectory not mounted) on a startup that
    also has a new doc to index, so the manifest IS rewritten. It must keep the
    keys it can't currently see, or the deleted doc returns once they reappear."""
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    store.delete_documents(ChromaDBStore.BUILTIN_COLLECTION, where={"source": str(corpus["beta"])})  # admin deletes beta
    finance = corpus["beta"].parent
    hidden = tmp_path / "finance-unmounted"
    finance.rename(hidden)
    (corpus["alpha"].parent / "new.md").write_text(_words(200, "new"), encoding="utf-8")
    assert asyncio.run(seed_builtin_knowledge(store=store)) > 0  # rewrites the manifest with a partial view

    hidden.rename(finance)  # the subdirectory comes back
    assert asyncio.run(seed_builtin_knowledge(store=store)) == 0
    assert not _rows_of(store, corpus["beta"])


def test_a_forced_reseed_with_no_files_leaves_the_manifest_alone(corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", tmp_path / "not-mounted")
    asyncio.run(seed_builtin_knowledge(store=store, force=True))
    assert store.read_seed_manifest(ChromaDBStore.BUILTIN_COLLECTION) == {"strategy/alpha.md", "finance/beta.md"}


# ---- issue #37: an install that moved on disk (stored `source` paths are stale)

def _move_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    moved = tmp_path / "moved-install" / "builtin"
    moved.parent.mkdir()
    (tmp_path / "builtin").rename(moved)
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", moved)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", moved / "failures")
    return moved


def test_a_forced_reseed_after_the_install_moved_replaces_rows_instead_of_duplicating_them(
    corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = corpus["store"]
    asyncio.run(seed_builtin_knowledge(store=store))
    count = store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION)
    old_ids = _ids(store, ChromaDBStore.BUILTIN_COLLECTION)
    moved = _move_install(tmp_path, monkeypatch)

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) == count, "no duplicate rows"
    assert not old_ids & _ids(store, ChromaDBStore.BUILTIN_COLLECTION), "the old-path rows are gone"
    got = store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(include=["metadatas"])
    assert all(m["source"].startswith(str(moved)) for m in got["metadatas"])


def test_a_pre_32_store_is_re_chunked_after_the_install_moved(
    corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old limitation: no row's source matched the new paths, so nothing re-chunked."""
    store = corpus["store"]
    old_alpha = tmp_path / "builtin" / "strategy" / "alpha.md"
    old = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", old_alpha)
    _move_install(tmp_path, monkeypatch)  # alpha now lives elsewhere; the stored path no longer exists

    asyncio.run(seed_builtin_knowledge(store=store))

    assert not set(old) & _ids(store, ChromaDBStore.BUILTIN_COLLECTION)
    assert _stale(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin") == {}


def test_a_row_is_not_taken_over_when_its_own_source_file_still_exists(corpus: dict[str, Any], tmp_path: Path) -> None:
    """Same corpus-relative location, but the stored source is a live file (a
    second checkout that still exists)."""
    store = corpus["store"]
    live = tmp_path / "elsewhere" / "builtin" / "strategy" / "alpha.md"
    live.parent.mkdir(parents=True)
    live.write_text(WORDS_FOR_37, encoding="utf-8")
    keep = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", live, tag="-live")

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert set(keep) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_a_stale_path_that_only_shares_the_filename_is_never_adopted(corpus: dict[str, Any], tmp_path: Path) -> None:
    """An API-authored doc from somewhere unrelated, same domain and filename as
    a shipped file: nothing about its location says it is that file."""
    store = corpus["store"]
    gone = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "old-install" / "strategy" / "alpha.md", tag="-amb")

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert set(gone) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_a_relative_stored_source_is_never_judged_against_the_working_directory(corpus: dict[str, Any]) -> None:
    store = corpus["store"]
    rel = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", Path("builtin/strategy/alpha.md"), tag="-rel")

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert set(rel) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_a_moved_install_is_recognised_only_at_the_same_corpus_relative_location(
    corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = corpus["store"]
    right = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "old" / "builtin" / "strategy" / "alpha.md", tag="-right")
    wrong = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "old" / "builtin" / "finance" / "alpha.md", tag="-wrong")
    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert not set(right) & _ids(store, ChromaDBStore.BUILTIN_COLLECTION), "same .../builtin/strategy/alpha.md location: superseded"
    assert set(wrong) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION), "different domain folder: not the same file"


WORDS_FOR_37 = " ".join(f"z{i}" for i in range(200))


# ---- round 2 review of #37

def test_an_unstatable_stored_path_counts_as_present_not_gone(
    corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stat failure that isn't "no such file" (permissions, a lost mount) must
    not let the row be adopted and deleted."""
    store = corpus["store"]
    unreachable = tmp_path / "old" / "builtin" / "strategy" / "alpha.md"
    keep = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", unreachable, tag="-perm")
    real_lstat = loader_mod.os.lstat

    def lstat(path: Any, *a: Any, **k: Any) -> Any:
        if str(path) == str(unreachable):
            raise PermissionError("mount lost")
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(loader_mod.os, "lstat", lstat)
    asyncio.run(seed_builtin_knowledge(store=store, force=True))
    assert set(keep) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_a_broken_symlink_as_stored_source_is_not_adopted(corpus: dict[str, Any], tmp_path: Path) -> None:
    store = corpus["store"]
    link = tmp_path / "old" / "builtin" / "strategy" / "alpha.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "nowhere.md")
    keep = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", link, tag="-link")
    asyncio.run(seed_builtin_knowledge(store=store, force=True))
    assert set(keep) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_a_blank_shipped_file_never_erases_the_rows_it_would_replace(corpus: dict[str, Any], tmp_path: Path) -> None:
    """A zero-byte file from a bad image build must not delete stored content."""
    store = corpus["store"]
    corpus["alpha"].write_text("", encoding="utf-8")
    keep = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "old" / "builtin" / "strategy" / "alpha.md", tag="-blank")
    exact = _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", corpus["alpha"], tag="-exact")

    asyncio.run(seed_builtin_knowledge(store=store, force=True))

    assert set(keep) | set(exact) <= _ids(store, ChromaDBStore.BUILTIN_COLLECTION)


def test_bootstrap_does_not_treat_an_unrelated_same_name_doc_as_the_shipped_one(corpus: dict[str, Any], tmp_path: Path) -> None:
    """No manifest yet; the only stored strategy/alpha.md rows come from an
    unrelated location. The shipped alpha.md is therefore new and gets indexed."""
    store = corpus["store"]
    _seed_old_style(store, ChromaDBStore.BUILTIN_COLLECTION, "builtin", tmp_path / "unrelated" / "strategy" / "alpha.md", tag="-other")
    added = asyncio.run(seed_builtin_knowledge(store=store))
    assert added == corpus["alpha_n"] + corpus["beta_n"]


def test_a_forced_reseed_after_a_move_also_replaces_failure_case_rows(
    corpus: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = corpus["store"]
    asyncio.run(seed_failures(store=store))
    count = store.get_collection_count(ChromaDBStore.FAILURES_COLLECTION)
    _move_install(tmp_path, monkeypatch)
    asyncio.run(seed_failures(store=store, force=True))
    assert store.get_collection_count(ChromaDBStore.FAILURES_COLLECTION) == count
