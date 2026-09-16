from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeStore(ABC):
    @abstractmethod
    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        collection: str,
    ) -> None: ...

    @abstractmethod
    def query(
        self,
        query_text: str,
        collection: str,
        domain_filter: list[str] | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def collection_exists(self, collection: str) -> bool: ...

    @abstractmethod
    def get_collection_count(self, collection: str) -> int: ...

    @abstractmethod
    def delete_documents(self, collection: str, where: dict[str, Any]) -> None: ...


class ChromaDBStore(KnowledgeStore):
    BUILTIN_COLLECTION = "builtin_knowledge"
    COMPANY_COLLECTION = "company_docs"
    FAILURES_COLLECTION = "failure_cases"
    # Web-research artifacts persisted from executive_research runs. Kept
    # SEPARATE from COMPANY_COLLECTION so unvetted, machine-generated
    # research never blends into curated company knowledge — it is
    # retrieved under its own clearly-labelled, lower-ranked section.
    RESEARCH_COLLECTION = "recent_research"
    # Synced Notion wiki pages. Kept SEPARATE from COMPANY_COLLECTION
    # because a Notion share is multi-writer and unreviewed — anyone
    # who can edit a shared page can inject text the agents will read.
    # Retrieved under its own clearly-labelled, lower-ranked section.
    NOTION_COLLECTION = "notion_wiki"
    # Files sent as attachments to bot integrations (Discord, Telegram,
    # web-chat upload). Kept SEPARATE from COMPANY_COLLECTION (issue #25)
    # for the same reason as NOTION_COLLECTION: any rostered/authorized
    # sender can put text here, not just an admin curating /documents, so
    # it is at least as multi-writer and unreviewed as a Notion share.
    # Retrieved under its own clearly-labelled, lower-ranked section.
    ATTACHMENT_COLLECTION = "attachment_uploads"

    def __init__(self, persist_directory: str | Path = "./chroma_db") -> None:
        import chromadb
        from chromadb.config import Settings

        self._client = chromadb.PersistentClient(
            path=str(persist_directory),
            settings=Settings(anonymized_telemetry=False),
        )

    def _get_or_create_collection(self, name: str) -> Any:
        return self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        collection: str = BUILTIN_COLLECTION,
    ) -> None:
        col = self._get_or_create_collection(collection)
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            col.upsert(
                documents=texts[i : i + batch_size],
                metadatas=metadatas[i : i + batch_size],
                ids=ids[i : i + batch_size],
            )

    def query(
        self,
        query_text: str,
        collection: str = BUILTIN_COLLECTION,
        domain_filter: list[str] | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        col = self._get_or_create_collection(collection)

        count = col.count()
        if count == 0:
            return []

        where: dict[str, Any] | None = None
        if domain_filter:
            if len(domain_filter) == 1:
                where = {"domain": domain_filter[0]}
            else:
                where = {"domain": {"$in": domain_filter}}

        query_kwargs: dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": min(n_results, count),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        results = col.query(**query_kwargs)

        output = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
                strict=False,
            ):
                output.append({"text": doc, "metadata": meta, "distance": dist})
        return output

    def collection_exists(self, collection: str) -> bool:
        try:
            self._client.get_collection(collection)
            return True
        except Exception:
            return False

    def get_collection_count(self, collection: str) -> int:
        try:
            col = self._client.get_collection(collection)
            return col.count()
        except Exception:
            return 0

    def delete_documents(self, collection: str, where: dict[str, Any]) -> None:
        try:
            col = self._get_or_create_collection(collection)
            col.delete(where=where)
        except Exception:
            pass

    def delete_company_docs(self) -> None:
        """Delete and recreate the company_docs collection, clearing all indexed documents."""
        import contextlib

        with contextlib.suppress(Exception):
            self._client.delete_collection(self.COMPANY_COLLECTION)
        # Recreate with the same HNSW settings so subsequent upserts work normally.
        self._get_or_create_collection(self.COMPANY_COLLECTION)

    def delete_notion_docs(self) -> None:
        """Drop synced Notion chunks from the isolated collection and any
        leftover COMPANY rows tagged ``type=notion`` (pre-isolation ingest)."""
        self.delete_documents(collection=self.NOTION_COLLECTION, where={"type": "notion"})
        self.delete_documents(collection=self.COMPANY_COLLECTION, where={"type": "notion"})

    def delete_attachment_docs(self) -> None:
        """Delete and recreate the attachment_uploads collection, clearing
        all ingested attachment chunks (issue #25). Mirrors
        ``delete_company_docs`` — attachment content is per-company like
        curated docs (not incrementally synced like Notion), so callers
        that clear company state on a company switch (fixture load/unload,
        factory reset, client-slot rebuild) must clear this alongside
        ``delete_company_docs()`` or a previous company's attachment
        uploads would silently survive into the new one."""
        import contextlib

        with contextlib.suppress(Exception):
            self._client.delete_collection(self.ATTACHMENT_COLLECTION)
        self._get_or_create_collection(self.ATTACHMENT_COLLECTION)

    def purge_expired_attachments(self, cutoff_epoch: float) -> int:
        """Delete attachment chunks whose ``ingested_at`` is older than
        ``cutoff_epoch`` (a unix-epoch float — see ``knowledge/loader.py``'s
        ``ingest_file``). Returns the number of chunks deleted.

        Unlike ``delete_attachment_docs()`` (a full-collection wipe used at
        company-switch boundaries), this is the issue #25 step 2 retention
        sweep: a partial, age-based purge that runs on a schedule
        (``knowledge/attachment_retention.py``) independent of any company
        switch. ``ingested_at`` is server-set at ingest time, never
        attacker-influenced, so there is no way for hostile content to
        forge itself a longer retention window.

        Chunks predating this fix have no ``ingested_at`` key at all (see
        ``ingest_file``'s docstring on why that can't be retroactively
        fixed) — Chroma's ``where`` comparison operators only match rows
        that HAVE the field, so those rows are silently skipped by this
        sweep rather than raising or being misinterpreted as "always
        expired." They remain reachable only via ``delete_attachment_docs()``
        or ``delete_company_docs()`` (wherever they actually live).
        """
        try:
            col = self._get_or_create_collection(self.ATTACHMENT_COLLECTION)
            where = {"ingested_at": {"$lt": cutoff_epoch}}
            matches = col.get(where=where, include=[])
            ids = matches.get("ids") or []
            if not ids:
                return 0
            col.delete(ids=ids)
            return len(ids)
        except Exception:
            # Unlike this file's other best-effort deletes (delete_documents,
            # collection_exists, get_collection_count), a silent failure here
            # is indistinguishable from "nothing was expired this tick" —
            # both report 0. For a control whose whole job is guaranteeing
            # deletion, that's the wrong failure mode: a permanently broken
            # sweep would look identical to a healthy one with nothing to do
            # (round-2 security review). Log loudly; still return 0 rather
            # than raise, so one bad tick can't crash the scheduler loop —
            # the next tick just retries against the same cutoff.
            logger.exception("attachment_retention.purge_expired_attachments: failed")
            return 0
