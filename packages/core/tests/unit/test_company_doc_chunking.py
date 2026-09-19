"""Regression tests for issue #31: company docs are chunked to fit the embedding window.

Chroma's default embedding model (all-MiniLM-L6-v2) embeds only the first 256
tokens of its input. Company docs used to be chunked at 512 words (~680 tokens
of technical prose), so roughly the last two thirds of every chunk was invisible
to vector search: on the clearpath_health policy docs only 2 of 8 specific facts
reached a specialist. Smaller chunks fix that; attachments deliberately keep
the old chunking because the chunk count also drives synchronous, loop-blocking
embedding work and that path accepts files from any rostered sender.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openexecutive.knowledge import loader as loader_mod
from openexecutive.knowledge.loader import (
    ATTACHMENT_CHUNK_OVERLAP,
    ATTACHMENT_CHUNK_WORDS,
    FINE_CHUNK_MAX_WORDS,
    FINE_CHUNK_OVERLAP,
    FINE_CHUNK_WORDS,
    chunk_text,
    ingest_file,
)
from openexecutive.knowledge.store import ChromaDBStore


class _FakeStore:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def add_documents(self, texts, metadatas, ids, collection):  # noqa: ANN001
        self.texts.extend(texts)


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def _ingest(path: Path, **kwargs: Any) -> _FakeStore:
    store = _FakeStore()
    asyncio.run(ingest_file(path, store, **kwargs))  # type: ignore[arg-type]
    return store


def _doc(tmp_path: Path, n_words: int) -> Path:
    p = tmp_path / "policy.md"
    p.write_text(_words(n_words), encoding="utf-8")
    return p


def test_constants_are_sane_and_attachments_keep_the_old_size() -> None:
    assert 0 <= FINE_CHUNK_OVERLAP < FINE_CHUNK_WORDS
    assert 0 <= ATTACHMENT_CHUNK_OVERLAP < ATTACHMENT_CHUNK_WORDS
    assert (ATTACHMENT_CHUNK_WORDS, ATTACHMENT_CHUNK_OVERLAP) == (512, 50)
    assert FINE_CHUNK_WORDS < ATTACHMENT_CHUNK_WORDS


def test_ingest_file_defaults_to_the_company_chunk_size(tmp_path: Path) -> None:
    store = _ingest(_doc(tmp_path, 1000))
    assert store.texts == chunk_text(_words(1000), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)
    assert len(store.texts) > 1
    assert all(len(t.split()) <= FINE_CHUNK_WORDS for t in store.texts)


def test_ingest_file_chunk_size_can_be_overridden(tmp_path: Path) -> None:
    store = _ingest(_doc(tmp_path, 1000), chunk_words=512, chunk_overlap=50)
    assert store.texts == chunk_text(_words(1000), 512, 50)
    assert len(store.texts) < len(chunk_text(_words(1000), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert max(len(t.split()) for t in store.texts) > FINE_CHUNK_WORDS


def test_a_doc_at_the_fine_chunking_limit_still_uses_company_chunks(tmp_path: Path) -> None:
    n = FINE_CHUNK_MAX_WORDS
    store = _ingest(_doc(tmp_path, n))
    assert store.texts == chunk_text(_words(n), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)


def test_a_huge_doc_falls_back_to_the_legacy_chunking_to_bound_embedding_work(tmp_path: Path) -> None:
    """Embedding is synchronous on the event loop (~14ms/chunk) and the fine
    size means ~4.6x more chunks, so an enormous file would freeze the process
    ~4.6x longer than before. Past the limit the old sizes apply -- nothing is
    dropped, and the worst case is exactly what it was pre-#31."""
    n = FINE_CHUNK_MAX_WORDS + 1
    store = _ingest(_doc(tmp_path, n))
    assert store.texts == chunk_text(_words(n), ATTACHMENT_CHUNK_WORDS, ATTACHMENT_CHUNK_OVERLAP)
    fine = len(chunk_text(_words(n), FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP))
    assert len(store.texts) * 4 < fine  # the work really is several times smaller


def test_the_fallback_log_line_uses_a_sanitized_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name is uploader-supplied (the slot-rebuild path passes no
    display_name, so it is the raw on-disk name); a newline must not forge log
    lines. Patch the module logger: caplog can't see it once an earlier test has
    built the app (api/main.py sets propagate=False on the openexecutive logger)."""
    from unittest.mock import MagicMock

    log = MagicMock()
    monkeypatch.setattr(loader_mod, "logger", log)
    path = tmp_path / "evil\nFORGED LOG LINE.md"
    path.write_text(_words(FINE_CHUNK_MAX_WORDS + 1), encoding="utf-8")

    _ingest(path)

    log.info.assert_called_once()
    logged_name = log.info.call_args.args[1]
    assert "\n" not in logged_name


def test_an_explicit_chunk_size_is_honoured_even_for_a_huge_doc(tmp_path: Path) -> None:
    n = FINE_CHUNK_MAX_WORDS + 1
    store = _ingest(_doc(tmp_path, n), chunk_words=60, chunk_overlap=5)
    assert store.texts == chunk_text(_words(n), 60, 5)


def test_chunk_words_and_overlap_must_be_passed_together(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="together"):
        _ingest(_doc(tmp_path, 300), chunk_words=100)
    with pytest.raises(ValueError, match="together"):
        _ingest(_doc(tmp_path, 300), chunk_overlap=10)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, 11), (10, -1)])
def test_chunk_text_rejects_parameters_that_would_never_advance(size: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_text("a b c d e f g h i j k l", size, overlap)


def test_ingest_file_has_no_await_still() -> None:
    # The #26 guard depends on ingest_file never yielding; a chunking change
    # must not have introduced one (test_attachment_knowledge_isolation pins
    # this too -- restated here because this change edited the function).
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(loader_mod.ingest_file))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Await)]


def _tokenizer_json() -> Path | None:
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    except Exception:
        return None
    hits = list(Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH).rglob("tokenizer.json"))
    return hits[0] if hits else None


# Dense, punctuation- and identifier-heavy prose (control ids, section marks,
# durations): the shape of a real security policy, ~1.6+ tokens per word.
_POLICY_SAMPLE = (
    "Per SOC 2 CC6.3 and HIPAA 164.312(a)(2)(iv), privileged accounts (IAM admin, DB superuser, "
    "break-glass) require MFA, quarterly access review within 5 business days, and CISO sign-off; "
    "SEV-1 incidents (ePHI/PCI exposure) page the on-call within 15 minutes, notify HHS-OCR within 60 days "
    "if >=500 individuals are affected, and preserve VPC flow logs, EBS snapshots & Panther alerts "
    "before containment. "
) * 40


@pytest.mark.skipif(_tokenizer_json() is None, reason="embedding model tokenizer not cached")
def test_every_company_chunk_fits_the_embedding_window() -> None:
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(_tokenizer_json()))
    tok.no_truncation()
    tok.no_padding()
    counts = [len(tok.encode(c).ids) for c in chunk_text(_POLICY_SAMPLE, FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP)]
    assert max(counts) <= 256, f"a company chunk exceeds the 256-token window: {max(counts)}"
    # ...and the old size really didn't fit, so this test isn't vacuous.
    old = [len(tok.encode(c).ids) for c in chunk_text(_POLICY_SAMPLE, ATTACHMENT_CHUNK_WORDS, ATTACHMENT_CHUNK_OVERLAP)]
    assert max(old) > 256


_FILLER = (
    "The warehouse forklift maintenance schedule requires weekly hydraulic inspection, monthly "
    "battery water checks, quarterly brake servicing, and an annual load test by a certified "
    "technician. Operators must log every shift start and report damaged pallets promptly. "
)
_FACT = (
    "The zebra migration quarantine protocol requires a fourteen day isolation period before release. "
    "During the zebra quarantine the herd is monitored twice daily by the migration quarantine officer, "
    "and the quarantine protocol is lifted only after two consecutive clean veterinary inspections."
)


def test_a_fact_late_in_a_document_is_retrievable_through_real_embeddings(tmp_path: Path) -> None:
    """The behavior #31 is about, end to end against a real Chroma collection:
    a fact ~400 words in sits past token 256 of a 512-word chunk, so the old
    chunking could not find it; company-size chunks can."""
    doc = tmp_path / "ops_policy.md"
    doc.write_text(_FILLER * 8 + _FACT + " " + _FILLER * 6, encoding="utf-8")
    query = "What does the zebra migration quarantine protocol require?"
    gate = 0.55  # KNOWLEDGE_DISTANCE_THRESHOLD default

    def best_distance(**chunking: int) -> float:
        store = ChromaDBStore(persist_directory=tmp_path / f"chroma_{len(chunking)}_{chunking.get('chunk_words', 0)}")
        asyncio.run(ingest_file(doc, store, collection=ChromaDBStore.COMPANY_COLLECTION, **chunking))
        rows = store.query(query_text=query, collection=ChromaDBStore.COMPANY_COLLECTION, n_results=3)
        return min(r["distance"] for r in rows)

    new = best_distance()  # default = company chunk size
    old = best_distance(chunk_words=512, chunk_overlap=50)

    assert new <= gate, f"company-size chunks should surface the late fact (distance {new:.2f})"
    assert old > gate, f"control: 512-word chunks should NOT (distance {old:.2f}) -- else this test proves nothing"
