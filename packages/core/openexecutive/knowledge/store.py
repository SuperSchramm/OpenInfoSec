from __future__ import annotations

import json
import logging
import os
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

        self.persist_directory = Path(persist_directory)
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

    def stale_chunk_sources(self, collection: str, where: dict[str, Any], current: str) -> dict[str, str | None]:
        """``{row id: source}`` for rows matching ``where`` whose ``chunking``
        marker (issue #32) is not ``current`` -- rows that predate the marker
        included. ``source`` is None when a row has none. Empty when nothing
        matches or the collection can't be read, so a read failure can never
        look like "stale rows to migrate"."""
        try:
            rows = self._client.get_collection(collection).get(where=where, include=["metadatas"])
        except Exception:
            return {}
        stale: dict[str, str | None] = {}
        for row_id, meta in zip(rows.get("ids") or [], rows.get("metadatas") or [], strict=False):
            meta = meta or {}
            if meta.get("chunking") != current:
                source = meta.get("source")
                stale[row_id] = source if isinstance(source, str) else None
        return stale

    def indexed_files(self, collection: str, where: dict[str, Any]) -> set[tuple[str, str]] | None:
        """``(domain, filename)`` of every document with rows matching ``where``.

        Keyed by name rather than ``source`` path so an install that moved on
        disk isn't mistaken for "all files are new". None when the collection
        can't be read: callers must treat that as "unknown", never as "empty"."""
        try:
            rows = self._client.get_collection(collection).get(where=where, include=["metadatas"])
        except Exception:
            return None
        return {
            (str(meta.get("domain")), str(meta.get("filename")))
            for meta in (rows.get("metadatas") or [])
            if meta
        }

    def _seed_manifest_path(self, collection: str) -> Path:
        return self.persist_directory / f"seed_manifest_{collection}.json"

    def read_seed_manifest(self, collection: str) -> set[str] | None:
        """Keys of the shipped files a previous startup already handled for
        ``collection`` (see loader._index_new_files). None when there is no
        manifest yet or it can't be read -- callers bootstrap from the rows."""
        try:
            data = json.loads(self._seed_manifest_path(collection).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, list) or not all(isinstance(k, str) for k in data):
            return None
        return set(data)

    def write_seed_manifest(self, collection: str, keys: set[str]) -> None:
        """Atomically record ``keys``. Best effort: a failure is logged and the
        next startup just bootstraps the manifest from the rows again."""
        path = self._seed_manifest_path(collection)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(sorted(keys)), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            logger.exception("write_seed_manifest: could not write %s", path)

    def stamp_chunking(self, collection: str, ids: list[str], version: str) -> None:
        """Set the ``chunking`` marker (issue #32) on existing rows, without
        re-embedding them. Called only once every batch of a document has been
        written, so a crash between batches leaves rows that still read as stale.
        Raises on failure: an unstamped file must not be reported as migrated."""
        col = self._get_or_create_collection(collection)
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            col.update(ids=batch, metadatas=[{"chunking": version}] * len(batch))

    def delete_ids(self, collection: str, ids: list[str]) -> None:
        """Delete rows by id. Logs and swallows failure: callers (the #32
        migration) leave the rows marked stale, so the next startup retries."""
        if not ids:
            return
        try:
            self._get_or_create_collection(collection).delete(ids=ids)
        except Exception:
            logger.exception("delete_ids: failed to delete %d rows from %s", len(ids), collection)

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
        uploads would silently survive into the new one.

        Also bumps the swap generation (issue #26) as its FIRST action, not
        after: unlike the other two callers (company switches, which already
        bump it via ``publish_swapped_store``), the admin-triggered manual
        purge route (``DELETE /knowledge/attachments``) calls this directly
        without a store swap. Without this bump, a background attachment
        ingest task scheduled just before this purge runs could still land
        its write after the purge completes, silently defeating the purge's
        "gone now" intent -- the exact same race this issue closed for
        company switches, just triggered by a different caller. Bumping
        first (security review round 2) rather than after the collection is
        rebuilt means the guard is armed even if ``_get_or_create_collection``
        below raises after a successful delete -- a partially-failed purge
        still disarms every in-flight ingest's stale write, not just a
        cleanly-completed one."""
        import contextlib

        from openexecutive.orchestrator.store_access import bump_store_generation

        bump_store_generation()
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
