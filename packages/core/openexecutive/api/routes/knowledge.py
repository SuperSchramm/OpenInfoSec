from __future__ import annotations

import contextlib
import errno
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from openexecutive.config import get_settings
from openexecutive.knowledge import overlay_fs
from openexecutive.knowledge.loader import (
    BUILTIN_KNOWLEDGE_PATH,
    DOMAIN_MAP,
    FAILURES_DIRNAME,
    FAILURES_KNOWLEDGE_PATH,
    MAX_KNOWLEDGE_DOC_CHARS,
    builtin_overlay_root,
    tombstone_path,
)
from openexecutive.knowledge.store import ChromaDBStore

router = APIRouter(prefix="/knowledge")

# Bounded (overlay_fs.MAX_DOC_STEM_CHARS) so the overlay's temp file still fits a
# 255-byte filesystem name limit.
_VALID_FILENAME = re.compile(rf"^[a-zA-Z0-9_\-]{{1,{overlay_fs.MAX_DOC_STEM_CHARS}}}\.md$")
_VALID_SOURCE_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")


ORIGIN_SHIPPED = "shipped"
ORIGIN_CUSTOM = "custom"
ORIGIN_EDITED = "edited"


class BuiltinFileMeta(BaseModel):
    domain: str
    filename: str
    size_bytes: int
    # ORIGIN_SHIPPED (from the image), ORIGIN_CUSTOM (added through the API) or
    # ORIGIN_EDITED (a shipped doc with an API-saved copy that takes precedence).
    origin: str = ORIGIN_SHIPPED


class BuiltinFileContent(BaseModel):
    domain: str
    filename: str
    content: str


class BuiltinFileWrite(BaseModel):
    domain: str
    filename: str
    # A blank document is indexed as nothing, yet would still cost an inode and a
    # block against a byte budget it never touches (issue #40).
    content: str = Field(max_length=MAX_KNOWLEDGE_DOC_CHARS)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class BuiltinListResponse(BaseModel):
    files: list[BuiltinFileMeta]


class BuiltinWriteResponse(BaseModel):
    domain: str
    filename: str
    chunks_indexed: int


def _validate_domain(domain: str) -> None:
    if domain not in DOMAIN_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {domain}")


def _validate_filename(filename: str) -> None:
    if not _VALID_FILENAME.match(filename):
        raise HTTPException(
            status_code=400,
            detail="Filename must be alphanumeric with dashes or underscores (at most 120 characters) and end in .md",
        )


# Documents written through the API go to the overlay folder on the persistent
# volume (issue #36), never into the package directory, which is the container
# image and is replaced on every deploy. Shipped files are never modified: an
# edit is an overlay copy that shadows the shipped file, and a delete of a
# shipped doc is a tombstone in the overlay. Reads prefer the overlay copy.


class _DocPaths(NamedTuple):
    shipped: Path
    custom: Path
    tombstone: Path

    def effective(self) -> Path | None:
        """The file a read should serve: the overlay copy, else the shipped one
        unless it was deleted through the API."""
        if self.custom.exists():
            return self.custom
        if self.shipped.exists() and not self.tombstone.exists():
            return self.shipped
        return None

    def origin(self) -> str:
        if self.custom.exists():
            return ORIGIN_EDITED if self.shipped.exists() else ORIGIN_CUSTOM
        return ORIGIN_SHIPPED


def _doc_paths(shipped_root: Path, overlay_root: Path, domain: str, filename: str) -> _DocPaths:
    paths = _DocPaths(
        shipped_root / domain / filename,
        overlay_root / domain / filename,
        tombstone_path(overlay_root, Path(domain) / filename),
    )
    # The overlay is a shared volume, a lower trust boundary than the read-only
    # image: never follow a symlink out of it (reads, writes or deletes). The
    # anchor is the TRUE overlay root, not overlay_root (the failures subfolder,
    # which could itself be the symlink).
    root = os.path.realpath(builtin_overlay_root())
    for path in (paths.custom, paths.tombstone):
        if not Path(os.path.realpath(path)).is_relative_to(root):
            raise HTTPException(status_code=400, detail="Path escapes the knowledge overlay")
    return paths


def _builtin_paths(domain: str, filename: str) -> _DocPaths:
    return _doc_paths(BUILTIN_KNOWLEDGE_PATH, builtin_overlay_root(), domain, filename)


def _failure_paths(domain: str, filename: str) -> _DocPaths:
    return _doc_paths(FAILURES_KNOWLEDGE_PATH, builtin_overlay_root() / FAILURES_DIRNAME, domain, filename)


def _list_files(shipped_root: Path, overlay_root: Path) -> list[BuiltinFileMeta]:
    files: list[BuiltinFileMeta] = []
    for domain in sorted(DOMAIN_MAP.keys()):
        names: set[str] = set()
        for root in (shipped_root, overlay_root):
            if (root / domain).is_dir():
                names.update(f.name for f in (root / domain).glob("*.md"))
        for name in sorted(names):
            try:
                paths = _doc_paths(shipped_root, overlay_root, domain, name)
            except HTTPException:  # a symlink out of the overlay: leave it out of the listing
                continue
            effective = paths.effective()
            if effective is not None:
                files.append(
                    BuiltinFileMeta(
                        domain=domain, filename=name, size_bytes=effective.stat().st_size, origin=paths.origin(),
                    )
                )
    return files


def _drop_rows(store: ChromaDBStore, collection: str, paths: _DocPaths) -> None:
    """Remove the indexed rows of a doc under either of its possible paths (a
    doc authored before issue #36 may still have rows under its old shipped path).

    ``delete_documents`` swallows every error, so verify the rows are really gone:
    a caller that then removes the file would otherwise leave content retrievable
    with no way to delete it (the retry would report 404).
    """
    for path in (paths.shipped, paths.custom):
        store.delete_documents(collection=collection, where={"source": str(path)})
        # None means "could not read the collection", which is NOT the same as "no
        # rows": delete_documents creates the collection if it is missing, so an
        # unreadable one here is a real failure, not a fresh install.
        remaining = store.all_chunk_sources(collection, {"source": str(path)})
        if remaining is None or remaining:
            raise HTTPException(status_code=500, detail="Indexed content could not be removed; retry")


def _overlay_error(exc: OSError) -> HTTPException:
    """Map an overlay filesystem failure to an HTTP error a caller can act on."""
    if isinstance(exc, overlay_fs.UnsafeOverlayPath):
        return HTTPException(status_code=400, detail="Path escapes the knowledge overlay")
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return HTTPException(status_code=507, detail="The knowledge overlay volume is full")
    return HTTPException(status_code=500, detail="The knowledge overlay could not be written")


def _write_overlay(paths: _DocPaths, content: str) -> None:
    root = builtin_overlay_root()
    custom_rel, tombstone_rel = paths.custom.relative_to(root), paths.tombstone.relative_to(root)
    previous = overlay_fs.file_size(root, custom_rel)
    new_size = len(content.encode("utf-8"))
    # The budget is a soft quota on the overlay's markdown bytes only (the embeddings
    # these documents produce live in the vector store and are not metered), and the
    # check and the write are separate steps, so concurrent workers can overshoot by
    # about one document each. It bounds disk growth, not embedding CPU: a same-size
    # rewrite costs nothing against it (issue #39 residual).
    # Only growth can breach the budget: shrinking an edit must stay possible even
    # when the overlay is already over a budget that was lowered later.
    used = overlay_fs.total_bytes(root, sweep_stale=True)  # one walk; also reclaims stale temp files
    if new_size > previous and used - previous + new_size > get_settings().builtin_overlay_max_bytes:
        raise HTTPException(status_code=413, detail="The knowledge overlay is full")
    existed = paths.custom.exists()
    try:
        overlay_fs.write_text(root, custom_rel, content)
        try:
            overlay_fs.unlink(root, tombstone_rel)  # re-creating a deleted shipped doc
        except OSError:
            if not existed:  # don't leave a half-created doc: a file with no rows and a 400 for the caller
                with contextlib.suppress(OSError):
                    overlay_fs.unlink(root, custom_rel)
            raise
    except OSError as exc:
        raise _overlay_error(exc) from exc


def _drop_new_doc_on_failure(store: ChromaDBStore, collection: str, paths: _DocPaths) -> None:
    """Clear stale rows for a doc being CREATED; if that fails, remove the file just
    written, so the caller's retry is not met with 409 for a doc that has no rows."""
    try:
        _drop_rows(store, collection, paths)
    except BaseException:
        with contextlib.suppress(OSError):
            overlay_fs.unlink(builtin_overlay_root(), paths.custom.relative_to(builtin_overlay_root()))
        raise


def _delete_doc(store: ChromaDBStore, collection: str, paths: _DocPaths) -> None:
    root = builtin_overlay_root()
    custom_rel, tombstone_rel = paths.custom.relative_to(root), paths.tombstone.relative_to(root)
    created_tombstone = False
    try:
        # Tombstone first: if it can't be written nothing has changed. Then the
        # rows, then the overlay copy. If the rows can't be removed the tombstone
        # is taken back, otherwise a shipped doc would vanish from the listing while
        # its chunks stay indexed and the retry would report 404. A failure removing
        # the overlay copy leaves it on disk, so the doc is still visible and a
        # retry of the DELETE works.
        if paths.shipped.exists():
            created_tombstone = overlay_fs.touch(root, tombstone_rel)  # atomic: only the creator may roll back
        try:
            _drop_rows(store, collection, paths)
        except BaseException:
            if created_tombstone:
                with contextlib.suppress(OSError):
                    overlay_fs.unlink(root, tombstone_rel)
            raise
        overlay_fs.unlink(root, custom_rel)
    except OSError as exc:
        raise _overlay_error(exc) from exc


def _get_store(request: Request):  # type: ignore[return]
    if hasattr(request.app.state, "store"):
        return request.app.state.store
    from openexecutive.config import get_settings

    return ChromaDBStore(persist_directory=get_settings().vector_store_path)


@router.get("/builtin", response_model=BuiltinListResponse)
async def list_builtin_files() -> BuiltinListResponse:
    return BuiltinListResponse(files=_list_files(BUILTIN_KNOWLEDGE_PATH, builtin_overlay_root()))


@router.get("/builtin/{domain}/{filename}", response_model=BuiltinFileContent)
async def get_builtin_file(domain: str, filename: str) -> BuiltinFileContent:
    _validate_domain(domain)
    _validate_filename(filename)
    path = _builtin_paths(domain, filename).effective()
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return BuiltinFileContent(domain=domain, filename=filename, content=path.read_text(encoding="utf-8"))


@router.post("/builtin", response_model=BuiltinWriteResponse)
async def create_builtin_file(body: BuiltinFileWrite, request: Request) -> BuiltinWriteResponse:
    _validate_domain(body.domain)
    _validate_filename(body.filename)
    paths = _builtin_paths(body.domain, body.filename)
    if paths.effective() is not None:
        raise HTTPException(status_code=409, detail="File already exists. Use PUT to update.")

    from openexecutive.knowledge.loader import ingest_builtin_file
    from openexecutive.knowledge.review_store import ContentType, ReviewStore

    store = _get_store(request)
    _write_overlay(paths, body.content)
    _drop_new_doc_on_failure(store, ChromaDBStore.BUILTIN_COLLECTION, paths)  # stale rows of a doc removed earlier
    chunks = await ingest_builtin_file(paths.custom, store)

    ReviewStore().register(
        item_id=f"builtin:{body.domain}:{body.filename}",
        content_type=ContentType.BUILTIN,
        domain=body.domain,
        filename=body.filename,
    )

    return BuiltinWriteResponse(domain=body.domain, filename=body.filename, chunks_indexed=chunks)


@router.put("/builtin/{domain}/{filename}", response_model=BuiltinWriteResponse)
async def update_builtin_file(
    domain: str, filename: str, body: BuiltinFileWrite, request: Request
) -> BuiltinWriteResponse:
    _validate_domain(domain)
    _validate_filename(filename)
    paths = _builtin_paths(domain, filename)
    if paths.effective() is None:
        raise HTTPException(status_code=404, detail="File not found. Use POST to create.")

    from openexecutive.knowledge.loader import ingest_builtin_file
    from openexecutive.knowledge.review_store import ContentType, ReviewStore

    store = _get_store(request)
    # The edit is saved as an overlay copy (the shipped file is left alone), so
    # the shipped doc's rows and any earlier overlay rows are replaced -- but only
    # AFTER the write succeeds: a full or read-only volume must not leave the doc
    # with no rows.
    _write_overlay(paths, body.content)
    _drop_rows(store, ChromaDBStore.BUILTIN_COLLECTION, paths)
    chunks = await ingest_builtin_file(paths.custom, store)

    rs = ReviewStore()
    item_id = f"builtin:{domain}:{filename}"
    rs.register(item_id=item_id, content_type=ContentType.BUILTIN, domain=domain, filename=filename)
    rs.touch_modified(item_id)

    return BuiltinWriteResponse(domain=domain, filename=filename, chunks_indexed=chunks)


# ---------------------------------------------------------------------------
# External / OER reference library (read-only)
# ---------------------------------------------------------------------------


class ExternalSourceInfo(BaseModel):
    id: str
    title: str
    publisher: str
    license: str
    phase: int
    domains: list[str]
    type: str
    url: str
    slug: str | None = None
    chunks: int
    files: int
    is_ingested: bool
    last_fetched_at: float | None = None


class ExternalSourcesResponse(BaseModel):
    sources: list[ExternalSourceInfo]
    total_chunks: int


class ExternalPeekChunk(BaseModel):
    domain: str
    filename: str
    chunk_index: int
    text: str


class ExternalPeekResponse(BaseModel):
    source_id: str
    chunks: list[ExternalPeekChunk]


def _validate_source_id(source_id: str) -> None:
    if not _VALID_SOURCE_ID.match(source_id):
        raise HTTPException(status_code=400, detail="Invalid source id")


def _cache_mtime(cache_dir: Path) -> float | None:
    """Newest mtime of any non-hidden file in the source's local cache.

    Used as a "last fetched at" indicator — that's when the on-disk artifact
    was last written, which is also when the most recent ingest read it.
    """
    if not cache_dir.exists():
        return None
    newest: float | None = None
    for p in cache_dir.rglob("*"):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(cache_dir).parts):
            continue
        m = p.stat().st_mtime
        if newest is None or m > newest:
            newest = m
    return newest


@router.get("/external", response_model=ExternalSourcesResponse)
async def list_external_sources(request: Request) -> ExternalSourcesResponse:
    """List every source declared in sources.yaml with live ingest stats."""
    from openexecutive.knowledge.external_sources import load_manifest

    manifest = load_manifest()
    store = _get_store(request)

    # Single bulk fetch is faster than per-source queries when there are 10+ sources.
    try:
        col = store._client.get_collection(ChromaDBStore.BUILTIN_COLLECTION)
        rows = col.get(include=["metadatas"])
    except Exception:
        rows = {"metadatas": []}

    chunk_counts: dict[str, int] = defaultdict(int)
    files_per_source: dict[str, set[str]] = defaultdict(set)
    for md in rows["metadatas"] or []:
        sid = md.get("source_id")
        if not sid:
            continue
        chunk_counts[sid] += 1
        if fn := md.get("filename"):
            files_per_source[sid].add(fn)

    sources: list[ExternalSourceInfo] = []
    for src in manifest:
        chunks = chunk_counts.get(src.id, 0)
        sources.append(
            ExternalSourceInfo(
                id=src.id,
                title=src.title,
                publisher=src.publisher,
                license=src.license,
                phase=src.phase,
                domains=src.domains,
                type=src.type,
                url=src.url,
                slug=src.slug,
                chunks=chunks,
                files=len(files_per_source.get(src.id, set())),
                is_ingested=chunks > 0,
                last_fetched_at=_cache_mtime(src.cache_dir),
            )
        )
    from openexecutive.knowledge.review_store import ReviewStore

    ingested = [
        {"id": s.id, "domains": s.domains}
        for s in sources
        if s.is_ingested
    ]
    if ingested:
        ReviewStore.sync_external_registrations(ingested)

    return ExternalSourcesResponse(sources=sources, total_chunks=sum(chunk_counts.values()))


@router.get("/external/{source_id}/peek", response_model=ExternalPeekResponse)
async def peek_external_source(
    source_id: str, request: Request, limit: int = 5
) -> ExternalPeekResponse:
    """Return the first N indexed chunks of a source so a human can spot-check them."""
    from openexecutive.knowledge.external_sources import load_manifest

    _validate_source_id(source_id)
    if not any(src.id == source_id for src in load_manifest()):
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_id}")

    limit = max(1, min(limit, 25))
    store = _get_store(request)
    try:
        col = store._client.get_collection(ChromaDBStore.BUILTIN_COLLECTION)
        rows = col.get(
            where={"source_id": source_id},
            limit=limit,
            include=["documents", "metadatas"],
        )
    except Exception:
        rows = {"documents": [], "metadatas": []}

    chunks = [
        ExternalPeekChunk(
            domain=md.get("domain", "?"),
            filename=md.get("filename", "?"),
            chunk_index=md.get("chunk_index", 0),
            text=doc,
        )
        for doc, md in zip(rows["documents"] or [], rows["metadatas"] or [], strict=False)
    ]
    return ExternalPeekResponse(source_id=source_id, chunks=chunks)


@router.delete("/builtin/{domain}/{filename}")
async def delete_builtin_file(domain: str, filename: str, request: Request) -> dict:
    _validate_domain(domain)
    _validate_filename(filename)
    paths = _builtin_paths(domain, filename)
    if paths.effective() is None:
        raise HTTPException(status_code=404, detail="File not found")

    from openexecutive.knowledge.review_store import ReviewStore

    _delete_doc(_get_store(request), ChromaDBStore.BUILTIN_COLLECTION, paths)
    ReviewStore().delete_item(f"builtin:{domain}:{filename}")
    return {"deleted": filename}


# ---------------------------------------------------------------------------
# Failures (negative learnings) — separate ChromaDB collection (failure_cases)
# but managed via the same CRUD shape as builtin playbooks. Chunked with the
# same default as seed_failures (loader.default_chunking) so a re-index produces
# the same artifacts whether done at seed-time or via PUT.
# ---------------------------------------------------------------------------



@router.get("/failures", response_model=BuiltinListResponse)
async def list_failure_files() -> BuiltinListResponse:
    return BuiltinListResponse(files=_list_files(FAILURES_KNOWLEDGE_PATH, builtin_overlay_root() / FAILURES_DIRNAME))


@router.get("/failures/{domain}/{filename}", response_model=BuiltinFileContent)
async def get_failure_file(domain: str, filename: str) -> BuiltinFileContent:
    _validate_domain(domain)
    _validate_filename(filename)
    path = _failure_paths(domain, filename).effective()
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return BuiltinFileContent(
        domain=domain, filename=filename, content=path.read_text(encoding="utf-8")
    )


@router.post("/failures", response_model=BuiltinWriteResponse)
async def create_failure_file(body: BuiltinFileWrite, request: Request) -> BuiltinWriteResponse:
    _validate_domain(body.domain)
    _validate_filename(body.filename)
    paths = _failure_paths(body.domain, body.filename)
    if paths.effective() is not None:
        raise HTTPException(status_code=409, detail="File already exists. Use PUT to update.")

    from openexecutive.knowledge.loader import ingest_builtin_file

    store = _get_store(request)
    _write_overlay(paths, body.content)
    _drop_new_doc_on_failure(store, ChromaDBStore.FAILURES_COLLECTION, paths)  # stale rows of a doc removed earlier
    chunks = await ingest_builtin_file(
        paths.custom,
        store,
        collection=ChromaDBStore.FAILURES_COLLECTION,
        chunk_type="failure_case",
    )
    return BuiltinWriteResponse(domain=body.domain, filename=body.filename, chunks_indexed=chunks)


@router.put("/failures/{domain}/{filename}", response_model=BuiltinWriteResponse)
async def update_failure_file(
    domain: str, filename: str, body: BuiltinFileWrite, request: Request
) -> BuiltinWriteResponse:
    _validate_domain(domain)
    _validate_filename(filename)
    paths = _failure_paths(domain, filename)
    if paths.effective() is None:
        raise HTTPException(status_code=404, detail="File not found. Use POST to create.")

    from openexecutive.knowledge.loader import ingest_builtin_file

    store = _get_store(request)
    _write_overlay(paths, body.content)  # write first; see update_builtin_file
    _drop_rows(store, ChromaDBStore.FAILURES_COLLECTION, paths)
    chunks = await ingest_builtin_file(
        paths.custom,
        store,
        collection=ChromaDBStore.FAILURES_COLLECTION,
        chunk_type="failure_case",
    )
    return BuiltinWriteResponse(domain=domain, filename=filename, chunks_indexed=chunks)


@router.delete("/failures/{domain}/{filename}")
async def delete_failure_file(domain: str, filename: str, request: Request) -> dict:
    _validate_domain(domain)
    _validate_filename(filename)
    paths = _failure_paths(domain, filename)
    if paths.effective() is None:
        raise HTTPException(status_code=404, detail="File not found")

    _delete_doc(_get_store(request), ChromaDBStore.FAILURES_COLLECTION, paths)
    return {"deleted": filename}


# ---------------------------------------------------------------------------
# Search — diagnostic "what would RAG retrieve for this question" endpoint.
# Returns raw structured chunks (text/metadata/distance) per collection so
# the UI can show exactly what each specialist would see. This is a read-only
# parallel of `retrieve()` and `retrieve_failures()` — it does NOT replace
# them or change the chat path.
# ---------------------------------------------------------------------------


_VALID_SOURCE_TYPES = {"builtin", "company", "failures", "external", "attachment"}

# Max characters of chunk text returned per search hit. UI shows ~600 chars,
# leaving headroom for "…" truncation indicator and tail context.
_SEARCH_SNIPPET_LIMIT = 800

# When both 'builtin' (no source_id) and 'external' (with source_id) are requested
# from BUILTIN_COLLECTION, we over-fetch from a single query and partition by
# source_id. The window must be large enough that a dominant category doesn't
# starve the other. We pull (n_builtin + n_external) * OVERFETCH_MULTIPLIER rows.
_BUILTIN_OVERFETCH_MULTIPLIER = 5

# Per-bucket result count ceiling (caller-supplied n_* values are clamped here).
_MAX_RESULTS_PER_BUCKET = 25


class KnowledgeSearchRequest(BaseModel):
    query: str
    domain_filter: list[str] | None = None
    specialist: str | None = None
    n_builtin: int = 5
    n_company: int = 3
    n_failures: int = 3
    n_external: int = 5
    n_attachment: int = 3
    include: list[str] | None = None  # subset of _VALID_SOURCE_TYPES


class SearchHit(BaseModel):
    filename: str
    domain: str
    source: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    license: str | None = None
    publisher: str | None = None
    chunk_index: int | None = None
    distance: float
    text: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    effective_domains: list[str] | None
    specialists_that_would_see_this: list[str]
    builtin: list[SearchHit]
    company: list[SearchHit]
    failures: list[SearchHit]
    external: list[SearchHit]
    attachment: list[SearchHit]


def _hits_from_chroma(
    rows: list[dict[str, object]], snippet_limit: int = _SEARCH_SNIPPET_LIMIT
) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for r in rows:
        md = r.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}
        text = str(r.get("text") or "")
        if len(text) > snippet_limit:
            text = text[:snippet_limit] + "…"
        chunk_index_raw = md.get("chunk_index")
        chunk_index = chunk_index_raw if isinstance(chunk_index_raw, int) else None
        hits.append(
            SearchHit(
                filename=str(md.get("filename", "unknown")),
                domain=str(md.get("domain", "?")),
                source=md.get("source") if isinstance(md.get("source"), str) else None,
                source_id=md.get("source_id") if isinstance(md.get("source_id"), str) else None,
                source_url=md.get("source_url") if isinstance(md.get("source_url"), str) else None,
                license=md.get("license") if isinstance(md.get("license"), str) else None,
                publisher=md.get("publisher") if isinstance(md.get("publisher"), str) else None,
                chunk_index=chunk_index,
                distance=float(r.get("distance", 0.0) or 0.0),  # type: ignore[arg-type]
                text=text,
            )
        )
    return hits


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    body: KnowledgeSearchRequest, request: Request
) -> KnowledgeSearchResponse:
    """Diagnostic: show what RAG would surface for a given query.

    Mirrors the per-collection queries the chat path uses but returns raw
    chunks (text/distance/metadata) instead of the formatted markdown blob
    `retrieve()` produces. Intended for the Knowledge UI's Query mode and
    for tuning the knowledge base offline.
    """
    from openexecutive.knowledge.retriever import DOMAIN_ALIASES, GENERAL_DOMAIN, _with_general

    if not body.query.strip():
        raise HTTPException(status_code=400, detail="query must be non-empty")

    include = set(body.include) if body.include else set(_VALID_SOURCE_TYPES)
    bad = include - _VALID_SOURCE_TYPES
    if bad:
        raise HTTPException(status_code=400, detail=f"Invalid include values: {sorted(bad)}")

    if body.domain_filter:
        for d in body.domain_filter:
            if d not in DOMAIN_MAP and d != GENERAL_DOMAIN:
                raise HTTPException(status_code=400, detail=f"Unknown domain: {d}")

    if body.specialist and body.specialist not in DOMAIN_ALIASES:
        raise HTTPException(status_code=400, detail=f"Unknown specialist: {body.specialist}")

    effective_domains: list[str] | None = body.domain_filter
    if effective_domains is None and body.specialist:
        effective_domains = DOMAIN_ALIASES.get(body.specialist)

    # Reverse-map: which specialists would see at least one of these domains?
    # "general" docs are visible to every specialist (issue #29), so a scope that
    # includes it -- like no scope at all -- is seen by all of them.
    if effective_domains and GENERAL_DOMAIN not in effective_domains:
        specialists_seeing = sorted(
            name
            for name, doms in DOMAIN_ALIASES.items()
            if any(d in doms for d in effective_domains)
        )
    else:
        specialists_seeing = sorted(DOMAIN_ALIASES.keys())

    store = _get_store(request)

    n_builtin = max(1, min(body.n_builtin, _MAX_RESULTS_PER_BUCKET))
    n_company = max(1, min(body.n_company, _MAX_RESULTS_PER_BUCKET))
    n_failures = max(1, min(body.n_failures, _MAX_RESULTS_PER_BUCKET))
    n_external = max(1, min(body.n_external, _MAX_RESULTS_PER_BUCKET))
    n_attachment = max(1, min(body.n_attachment, _MAX_RESULTS_PER_BUCKET))

    def _query_collection(collection: str, n: int) -> list[dict[str, object]]:
        try:
            return store.query(
                query_text=body.query,
                collection=collection,
                domain_filter=effective_domains,
                n_results=n,
            )
        except Exception:
            return []

    def _query_collection_unscoped(collection: str, n: int) -> list[dict[str, object]]:
        """Same as ``_query_collection`` but never applies a domain filter —
        for collections whose items carry no reliable per-domain tag
        (round-2 security review: ATTACHMENT_COLLECTION chunks are tagged
        domain="company_docs", which matches no real DOMAIN_MAP value, so
        a domain-scoped query against it would silently return zero rows
        every time; mirrors ``retrieve()``'s identical treatment)."""
        try:
            return store.query(
                query_text=body.query,
                collection=collection,
                domain_filter=None,
                n_results=n,
            )
        except Exception:
            return []

    builtin_hits: list[SearchHit] = []
    external_hits: list[SearchHit] = []
    want_builtin = "builtin" in include
    want_external = "external" in include
    if want_builtin or want_external:
        # builtin + external share BUILTIN_COLLECTION — only metadata source_id
        # distinguishes them. Single over-fetched query, then partition.
        # The window must be wide enough that a dominant category (e.g. heavy
        # OER ingest) doesn't crowd out the requested count in the other.
        wanted_builtin = n_builtin if want_builtin else 0
        wanted_external = n_external if want_external else 0
        window = (wanted_builtin + wanted_external) * _BUILTIN_OVERFETCH_MULTIPLIER
        raw = _query_collection(ChromaDBStore.BUILTIN_COLLECTION, window)
        for r in raw:
            md = r.get("metadata") or {}
            md_dict = md if isinstance(md, dict) else {}
            if md_dict.get("source_id"):
                if want_external and len(external_hits) < wanted_external:
                    external_hits.extend(_hits_from_chroma([r]))
            else:
                if want_builtin and len(builtin_hits) < wanted_builtin:
                    builtin_hits.extend(_hits_from_chroma([r]))
            if (
                len(builtin_hits) >= wanted_builtin
                and len(external_hits) >= wanted_external
            ):
                break

    company_hits: list[SearchHit] = []
    if "company" in include:
        # Mirror retrieve(): the COMPANY query also matches "general"-tagged docs
        # (issue #29). This endpoint exists to answer "would specialist X have
        # seen this doc?" -- it must not disagree with the chat path.
        try:
            company_raw = store.query(
                query_text=body.query,
                collection=ChromaDBStore.COMPANY_COLLECTION,
                domain_filter=_with_general(effective_domains),
                n_results=n_company,
            )
        except Exception:
            company_raw = []
        company_hits = _hits_from_chroma(company_raw)

    failure_hits: list[SearchHit] = []
    if "failures" in include:
        failure_hits = _hits_from_chroma(
            _query_collection(ChromaDBStore.FAILURES_COLLECTION, n_failures)
        )

    # issue #25 security review round 1: without this bucket, an operator
    # had no way to search/peek/enumerate attachment_uploads at all once
    # it stopped sharing COMPANY_COLLECTION with /documents uploads —
    # detection tooling regressed even though the trust boundary improved.
    # Unscoped (round 2): a domain-filtered query here would silently
    # return zero rows every time — see _query_collection_unscoped.
    attachment_hits: list[SearchHit] = []
    if "attachment" in include:
        attachment_hits = _hits_from_chroma(
            _query_collection_unscoped(ChromaDBStore.ATTACHMENT_COLLECTION, n_attachment)
        )

    return KnowledgeSearchResponse(
        query=body.query,
        effective_domains=effective_domains,
        specialists_that_would_see_this=specialists_seeing,
        builtin=builtin_hits,
        company=company_hits,
        failures=failure_hits,
        external=external_hits,
        attachment=attachment_hits,
    )


class AttachmentPurgeResponse(BaseModel):
    purged: int


@router.delete("/attachments", response_model=AttachmentPurgeResponse)
async def purge_attachments(request: Request) -> AttachmentPurgeResponse:
    """Immediately wipe the ENTIRE ATTACHMENT_COLLECTION (issue #25 step 2)
    — every attachment chunk from every sender, not a targeted delete.

    The periodic retention sweep (knowledge/attachment_retention.py) closes
    the unbounded-growth half of #25 on a schedule; this route is the other
    half — immediate remediation when an operator wants attachment content
    gone now rather than waiting out the retention window. Same auth as
    every other mutating route here: the app-wide shared-secret middleware
    (this path is not in api/main.py's _UNAUTHENTICATED_PATHS allowlist).

    Full-collection wipe, not a filtered delete — matches
    ChromaDBStore.delete_attachment_docs()'s existing (issue #25 step 1)
    semantics, used identically at fixture/reset/client-switch boundaries.
    There is no per-document attachment purge today (no stable per-upload
    identifier survives the deliberate anti-collision chunk-id design from
    issue #22), so this cannot target one bad upload without also removing
    every other attachment currently indexed — only "purge everything" or
    "wait for the TTL."
    """

    store = _get_store(request)
    purged = store.get_collection_count(ChromaDBStore.ATTACHMENT_COLLECTION)
    store.delete_attachment_docs()
    return AttachmentPurgeResponse(purged=purged)
