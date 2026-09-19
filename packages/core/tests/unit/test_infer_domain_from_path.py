"""Regression tests for issue #35: a document's domain comes from the folders that
are part of the corpus layout, never from wherever the checkout happens to live.

``infer_domain_from_path`` used to scan every part of the ABSOLUTE path, so a repo
or company-docs directory under a folder named like a domain (``~/legal/...``,
``/srv/security/...``) tagged every document with it -- built-in docs with the
wrong domain, company docs hidden from every specialist but that domain's.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openexecutive.knowledge import loader as loader_mod
from openexecutive.knowledge.loader import (
    infer_domain_from_path,
    ingest_builtin_file,
    ingest_file,
    seed_builtin_knowledge,
)
from openexecutive.knowledge.store import ChromaDBStore

WORDS = " ".join(f"w{i}" for i in range(200))


def test_domain_comes_from_the_folders_under_the_root() -> None:
    root = Path("/repo/knowledge/builtin")
    assert infer_domain_from_path(root / "security" / "nist.md", root) == "security"
    assert infer_domain_from_path(root / "security" / "sub" / "nist.md", root) == "security"
    assert infer_domain_from_path(root / "failures" / "legal" / "ftx.md", root) == "legal"
    assert infer_domain_from_path(root / "loose.md", root) == "general"
    assert infer_domain_from_path(root / "Security" / "x.md", root) == "security"


@pytest.mark.parametrize("parent", ["legal", "security", "finance", "hr", "product", "strategy", "operations"])
def test_a_domain_named_folder_above_the_root_is_ignored(parent: str) -> None:
    root = Path("/home/dev") / parent / "checkout" / "builtin"
    assert infer_domain_from_path(root / "marketing" / "x.md", root) == "marketing"
    assert infer_domain_from_path(root / "x.md", root) == "general"


def test_without_a_root_only_the_immediate_parent_folder_counts() -> None:
    assert infer_domain_from_path(Path("/home/legal/company/docs/policy.md")) == "general"
    assert infer_domain_from_path(Path("/tmp/finance/x/report.md")) == "general"
    assert infer_domain_from_path(Path("/tmp/uploads/finance/report.md")) == "finance"


def test_a_path_outside_the_root_falls_back_to_its_immediate_parent() -> None:
    assert infer_domain_from_path(Path("/elsewhere/hr/x.md"), Path("/repo/builtin")) == "hr"
    assert infer_domain_from_path(Path("/elsewhere/legal/deep/x.md"), Path("/repo/builtin")) == "general"


def test_a_built_in_corpus_installed_under_a_domain_named_folder_is_tagged_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The issue's repro: the whole install lives under .../legal/..."""
    builtin = tmp_path / "legal" / "checkout" / "builtin"
    (builtin / "strategy").mkdir(parents=True)
    (builtin / "finance").mkdir()
    (builtin / "strategy" / "deeper").mkdir()
    (builtin / "strategy" / "deeper" / "c.md").write_text(WORDS, encoding="utf-8")  # nested: needs the corpus root
    (builtin / "strategy" / "a.md").write_text(WORDS, encoding="utf-8")
    (builtin / "finance" / "b.md").write_text(WORDS, encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", builtin)
    monkeypatch.setattr(loader_mod, "FAILURES_KNOWLEDGE_PATH", builtin / "failures")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")

    asyncio.run(seed_builtin_knowledge(store=store))

    got = store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(include=["metadatas"])
    by_file: dict[str, set[str]] = {}
    for meta in got["metadatas"]:
        by_file.setdefault(meta["filename"], set()).add(meta["domain"])
    assert by_file == {"a.md": {"strategy"}, "b.md": {"finance"}, "c.md": {"strategy"}}


def test_a_single_built_in_file_under_a_domain_named_install_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builtin = tmp_path / "security" / "checkout" / "builtin"
    (builtin / "hr").mkdir(parents=True)
    path = builtin / "hr" / "onboarding.md"
    path.write_text(WORDS, encoding="utf-8")
    monkeypatch.setattr(loader_mod, "BUILTIN_KNOWLEDGE_PATH", builtin)
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")

    asyncio.run(ingest_builtin_file(path, store))

    got = store._get_or_create_collection(ChromaDBStore.BUILTIN_COLLECTION).get(include=["metadatas"])
    assert {m["domain"] for m in got["metadatas"]} == {"hr"}


def test_company_docs_under_a_domain_named_directory_stay_visible_to_every_specialist(tmp_path: Path) -> None:
    """The harmful direction: ~/finance/company/docs/*.md used to tag every
    company doc "finance", hiding it from every other specialist."""
    docs = tmp_path / "finance" / "company" / "docs"
    docs.mkdir(parents=True)
    doc = docs / "incident_response_plan.md"
    doc.write_text(WORDS, encoding="utf-8")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")

    asyncio.run(ingest_file(path=doc, store=store, collection=ChromaDBStore.COMPANY_COLLECTION))

    got = store._get_or_create_collection(ChromaDBStore.COMPANY_COLLECTION).get(include=["metadatas"])
    assert {m["domain"] for m in got["metadatas"]} == {"general"}


def test_an_explicit_domain_still_wins(tmp_path: Path) -> None:
    doc = tmp_path / "legal" / "x.md"
    doc.parent.mkdir()
    doc.write_text(WORDS, encoding="utf-8")
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    asyncio.run(ingest_file(path=doc, store=store, domain="hr", collection=ChromaDBStore.COMPANY_COLLECTION))
    got: dict[str, Any] = store._get_or_create_collection(ChromaDBStore.COMPANY_COLLECTION).get(include=["metadatas"])
    assert {m["domain"] for m in got["metadatas"]} == {"hr"}
