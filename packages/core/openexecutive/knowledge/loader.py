from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from openexecutive.knowledge.store import ChromaDBStore

logger = logging.getLogger(__name__)

BUILTIN_KNOWLEDGE_PATH = Path(__file__).parent / "builtin"
FAILURES_KNOWLEDGE_PATH = BUILTIN_KNOWLEDGE_PATH / "failures"

DOMAIN_MAP: dict[str, str] = {
    "strategy": "strategy",
    "finance": "finance",
    "hr": "hr",
    "legal": "legal",
    "operations": "operations",
    "marketing": "marketing",
    "board": "board",
    "product": "product",
    "security": "security",
    "governance": "governance",
    "compliance": "compliance",
}


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end == len(words):
            break
        start = end - overlap
    return chunks


def extract_text_from_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def extract_text_from_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_text_from_xlsx(path: Path, max_chars: int = 200_000) -> str:
    """Flatten an .xlsx/.xlsm workbook to text — one ``## <sheet>`` heading per
    NON-EMPTY worksheet, cells tab-joined and rows newline-joined. Only reads
    stored cell values (``data_only=True`` returns cached formula results, not
    formulae); legacy binary ``.xls`` is not supported by openpyxl.

    ``read_only`` streams rows and ``max_chars`` bounds the accumulated text, so
    a decompression-bombed workbook (a small archive that inflates to millions
    of cells) can't exhaust memory."""
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        parts: list[str] = []
        total = 0
        for ws in wb.worksheets:
            heading_written = False
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if not cells:
                    continue
                if not heading_written:
                    # Defer the heading until the sheet is known to have data,
                    # so a fully-blank sheet contributes nothing.
                    heading = f"## {ws.title}"
                    parts.append(heading)
                    total += len(heading) + 1
                    heading_written = True
                line = "\t".join(cells)
                parts.append(line)
                total += len(line) + 1
                if total >= max_chars:
                    return "\n".join(parts)
        return "\n".join(parts)
    finally:
        wb.close()


def extract_text_from_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(path)
    elif suffix in (".docx", ".doc"):
        return extract_text_from_docx(path)
    elif suffix in (".xlsx", ".xlsm"):
        return extract_text_from_xlsx(path)
    elif suffix in (".md", ".txt", ".rst", ".csv"):
        return path.read_text(encoding="utf-8")
    return ""


def _make_chunk_id(source: str, chunk_index: int) -> str:
    base = f"{source}::chunk::{chunk_index}"
    return hashlib.md5(base.encode()).hexdigest()


def infer_domain_from_path(path: Path) -> str:
    for part in path.parts:
        domain = DOMAIN_MAP.get(part.lower())
        if domain:
            return domain
    return "general"


# Strips control characters (C0 + C1 ranges, covering \r\n\t\x0b\x0c and the
# less-obvious \x1b ESC / \x85 NEL), the Unicode line/paragraph separators
# models routinely treat as newlines, and the [ ] brackets that delimit
# retriever.py's `[filename] chunk text` citation format. Deliberately a
# denylist, not an allowlist (issue #22 round 2 review flagged this as
# incomplete protection against a merely-suspicious-looking-but-technically-
# unblocked filename like "report (VERIFIED BY CEO).md" -- an allowlist
# would close that too, at the cost of also rejecting legitimate unicode
# filenames; not attempted here) -- this pass targets the structural
# characters that could forge a fake citation boundary, not general
# untrustworthiness, which the surrounding text already carries regardless
# of the filename (see the "Security note" on extracted text a few
# functions up in this file).
_UNSAFE_DISPLAY_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  \[\]]")
_MAX_DISPLAY_NAME_CHARS = 255


def _sanitize_display_name(raw: str) -> str:
    """Strip path components and characters that could forge a RAG
    attribution label boundary — see ``_UNSAFE_DISPLAY_NAME_CHARS`` above.
    Capped length guards against an absurdly long filename bloating every
    citation.

    NFKC-normalizes first so lookalike characters collapse onto the ones
    the regex actually targets — e.g. fullwidth brackets (U+FF3B/FF3D) fold
    to plain ``[``/``]`` and get stripped, and NBSP/ideographic space fold
    to a plain space. Also drops every Unicode "format" (category Cf)
    character — zero-width space/joiners, bidi override marks, soft
    hyphen — which render invisibly but would otherwise let two
    differently-spelled display names be treated as the same identity by
    anything that compares this string by eye. This does not defend
    against homoglyphs (e.g. Cyrillic "а" for Latin "a"): NFKC doesn't fold
    those, and doing so generally needs a confusables table, not attempted
    here — same "structural, not general trust" scope as the denylist
    comment above.
    """
    name = Path(raw).name  # strip any path components
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cf")
    name = _UNSAFE_DISPLAY_NAME_CHARS.sub("", name).strip()
    return (name or "unnamed")[:_MAX_DISPLAY_NAME_CHARS]


# Real front-matter is a handful of short lines. A larger block is not
# front-matter worth parsing: it is left alone rather than handed to the YAML
# parser (see the RecursionError note in _split_front_matter_domain). 1024 is
# measured, not arbitrary: worst-case nested-flow input costs ~0.045s to fail
# at this size but ~0.4s at 2048+, which across a fixture of many docs would
# stall a load that runs after the collection has already been wiped.
_MAX_FRONT_MATTER_CHARS = 1024

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

# Domains a company doc may be declared under. "general" = not specific to one
# domain (visible to every specialist -- see retriever._with_general); "talent"
# is accepted for COMPANY docs even though BUILTIN has no such subtree.
_DECLARABLE_DOMAINS = frozenset({*DOMAIN_MAP.values(), "general", "talent"})


def _split_front_matter_domain(text: str, name: str) -> tuple[str | None, str]:
    """Return ``(declared_domain, text_without_front_matter)``.

    A markdown doc may open with a front-matter block carrying ``domain:
    <name>`` (issue #29) so its author -- e.g. a fixture -- can say which
    specialists' retrieval it belongs to, instead of relying on the on-disk
    path (``company/docs/*.md`` has no domain segment, so path inference
    yields "general"). Only called when the caller has opted in
    (``ingest_file(honor_front_matter=True)``); the block is metadata, not
    content, and is stripped only when it declares a ``domain`` key, so a doc
    with unrelated front-matter is indexed exactly as before. An unknown or
    non-string domain is ignored with a warning (the doc still indexes, as
    "general"), never guessed at -- a typo must not silently hide a doc behind
    a domain no specialist queries.

    Never raises: this runs inside loops (fixture load, client switch) that
    have already wiped the collection, so one hostile file must not be able to
    abort the re-index. ``yaml.safe_load`` can raise more than ``YAMLError``
    (deeply nested flow collections raise ``RecursionError``), hence the broad
    ``except``, plus a size bound so a huge block is never parsed at all.
    """
    # A UTF-8 BOM survives ``read_text(encoding="utf-8")`` (Windows Notepad
    # writes one) and would otherwise defeat the ``\A---`` anchor.
    probe = text[1:] if text.startswith("\ufeff") else text
    match = _FRONT_MATTER.match(probe)
    if not match:
        return None, text
    if len(match.group(1)) > _MAX_FRONT_MATTER_CHARS:
        # Left unstripped and unparsed, so it indexes as ordinary content under
        # "general" -- but say so: the author asked for a domain and didn't get it.
        logger.warning(
            "ingest_file: front-matter block in %s is over %d chars -- not parsed, "
            "doc indexed as-is",
            _sanitize_display_name(name), _MAX_FRONT_MATTER_CHARS,
        )
        return None, text
    try:
        meta = yaml.safe_load(match.group(1))
    except Exception:  # noqa: BLE001 -- see the docstring: must never raise
        logger.warning(
            "ingest_file: front-matter in %s is not valid YAML -- ignored, doc "
            "indexed as-is",
            _sanitize_display_name(name),
        )
        return None, text
    if not isinstance(meta, dict) or "domain" not in meta:
        return None, text
    remainder = probe[match.end():]
    declared = meta["domain"]
    if isinstance(declared, str) and declared.strip().lower() in _DECLARABLE_DOMAINS:
        return declared.strip().lower(), remainder
    logger.warning(
        "ingest_file: ignoring front-matter domain %r in %s -- not one of %s",
        declared, _sanitize_display_name(name), sorted(_DECLARABLE_DOMAINS),
    )
    return None, remainder


async def ingest_file(
    path: Path,
    store: ChromaDBStore,
    domain: str | None = None,
    collection: str = ChromaDBStore.COMPANY_COLLECTION,
    *,
    display_name: str | None = None,
    expected_generation: int | None = None,
    honor_front_matter: bool = False,
) -> int:
    """Extract, chunk, and upsert one file's text into ``collection``.

    ``display_name`` (issue #22): when the caller reads ``path`` from a
    throwaway location — a ``tempfile.NamedTemporaryFile`` that's deleted
    right after this call, as both the attachment-ingest path and the
    ``/documents`` upload route do — pass the real uploaded filename here.
    It becomes the ``filename``/``source`` metadata (sanitized — see
    ``_sanitize_display_name``) instead of the temp path, so an ingested
    chunk is identifiable by its real name after the temp file no longer
    exists. Identifiable is not the same as purgeable-by-name for every
    caller: the ``/documents`` route pairs this with its own explicit
    ``delete_documents(where={"filename": ...})`` call before ingesting
    (see that route — safe there because it sits behind the app-wide
    shared-secret auth gate), but the attachment-ingest path has no
    per-upload purge — issue #25 step 2 closed the growth/remediation gap
    with a different mechanism instead (a daily age-based retention sweep,
    ``attachment_retention.py``, plus an admin-triggered full-collection
    wipe, ``DELETE /knowledge/attachments`` -> ``delete_attachment_docs()``),
    not a per-filename delete. (Issue #25 step 1 — the attachment path no
    longer *shares* a
    collection with curated ``/documents`` uploads at all; see
    ``ChromaDBStore.ATTACHMENT_COLLECTION`` and the ``collection`` param
    below. Chunks written to that collection also get ``type="attachment"``
    metadata, mirroring how Notion content is tagged ``type="notion"``.
    That isolation is what makes step 2's sweep-and-wipe remediation safe
    to run unconditionally against the whole collection -- it can never
    touch curated ``/documents`` content by construction.)

    Deliberately does NOT derive the chunk id from ``display_name`` — an
    earlier version of this fix did, to also get automatic dedup on
    re-upload, but that made ``display_name`` (attacker-influenced on the
    attachment-ingest path) a shared collision key: two unrelated uploads
    sharing a filename would upsert over the SAME chunk ids and silently
    destroy each other's content. A per-surface namespace prefix was tried
    next and still didn't close it — an unsanitized ``:`` in the filename
    could forge a fake namespace prefix, and every sender within one
    surface (e.g. every Discord user) still shared one namespace, so one
    rostered user could already overwrite another's upload. Closing that
    properly needs per-uploader identity threaded through 3 integration
    surfaces, which issue #22 itself explicitly hedged as only "ideally"
    wanted, not required. Chunk ids stay derived from ``path`` (unique per
    call — every caller either reads a fresh tempfile or a real path that's
    already unique by construction), so re-uploads accumulate distinct
    chunk sets by default — callers that want overwrite-on-reupload (like
    ``/documents``) get it by deleting the old set by filename themselves
    first, not by relying on colliding ids.

    ``expected_generation`` (issue #26): the caller's snapshot of
    ``orchestrator.store_access.get_store_generation()``, re-checked here
    immediately before the actual write below. This closes a race that's
    specific to ``integrations/attachments.py``'s ``_schedule_ingest``: a
    fire-and-forget background ingest task, scheduled via
    ``loop.create_task()``, that a company switch or an admin attachment
    purge can wipe/swap the store out from under before that task has even
    had its first turn on the event loop (see the next paragraph for why
    it's specifically *that* window and not the extraction time itself).

    The capture point matters more than it looks: it must happen in the
    caller's *synchronous* code, before the background task is even
    scheduled onto the event loop -- not inside the task once it starts
    running. This function has no ``await`` anywhere in it, so once a
    caller's background task gets a turn on the loop it runs start-to-finish
    (extraction, this check, the write) as one atomic step with no
    interleaving possible; the only real window in which another coroutine
    can run and bump the generation is *before* that task starts. A capture
    taken after the task is already scheduled can itself observe a bump
    that happened in that gap, making this check compare a stale value
    against itself and never catch anything -- see ``_schedule_ingest``'s
    comment for where the capture actually happens.

    If the generation has advanced, the write is skipped rather than
    landing in whatever now lives under ``collection``, and this returns
    ``-1`` rather than ``0`` (round 2, logic review) -- ``0`` already means
    "extracted no text" (see the early return right below), and a caller
    that only checked ``count == 0`` to decide whether to log a race-skip
    could misattribute an empty/unreadable attachment that also happened to
    coincide with an unrelated switch as if the switch were the reason
    nothing was written, when the two are unrelated. Callers that ingest
    synchronously in the same request/turn that reads ``store`` have no
    such window and leave this ``None``, so they never see ``-1``.

    Domain resolution (issue #29): an explicit ``domain`` argument wins; else,
    if the caller passed ``honor_front_matter=True``, for a ``.md`` file, a
    ``domain:`` declared in its front-matter (see
    ``_split_front_matter_domain``); else the path-based inference, which
    falls back to "general" (visible to every specialist).

    ``honor_front_matter`` is opt-in and off by default, and only the fixture
    loader turns it on. Front-matter is document CONTENT, so honoring it
    wherever a doc is re-read would let its author override the tag an admin
    chose at upload (``/documents`` passes an explicit domain; the same file
    re-ingested on a client switch would then be re-tagged by its own body),
    and would let a sender pick their own tag. A fixture is chosen by an
    operator and loaded as a set (a *generated* fixture is model-authored, but
    goes through a review step before it is saved), which is the one place an
    author-declared tag is wanted.

    A declared tag applies at fixture-load time only. Anything that later
    re-indexes the active docs directory without a fixture load (e.g. a
    client-slot rebuild) tags each doc from its path again -- "general", which
    every specialist can see -- so the drift is always in the permissive
    direction, never toward hiding a doc.
    """
    text = extract_text_from_file(path)
    declared_domain: str | None = None
    if honor_front_matter and domain is None and path.suffix.lower() == ".md":
        declared_domain, text = _split_front_matter_domain(text, path.name)
    if not text.strip():
        return 0

    inferred_domain = domain or declared_domain or infer_domain_from_path(path)
    chunks = chunk_text(text, chunk_size=512, overlap=50)

    name = _sanitize_display_name(display_name) if display_name else path.name
    source_label = name if display_name else str(path)

    # type="attachment" (issue #25 security review round 1): derived from
    # `collection`, never a separate param, so the tag can never drift from
    # the collection a chunk actually lives in. Doesn't retroactively fix
    # attachment chunks ingested before this fix (they're already sitting,
    # untagged, in COMPANY_COLLECTION, indistinguishable from curated docs
    # — see the issue #25 closing notes on why that gap has no clean
    # automated remediation) — this is defense-in-depth for every
    # ATTACHMENT_COLLECTION write from here on, mirroring how Notion
    # content already carries ``type="notion"`` for the same reason.
    # ingested_at (issue #25 step 2): a numeric unix-epoch float, not an
    # ISO string -- ChromaDB's where-filter $lt/$gt comparisons need a
    # numeric metadata type (verified directly against a live collection
    # before choosing this). Server-set at ingest time, never derived from
    # anything attacker-supplied (unlike display_name/filename), so it
    # can't be used to forge a longer retention window for hostile
    # content. Powers attachment_retention.py's periodic expiry sweep.
    extra_metadata: dict[str, Any] = (
        {"type": "attachment", "ingested_at": time.time()}
        if collection == ChromaDBStore.ATTACHMENT_COLLECTION
        else {}
    )

    texts = chunks
    metadatas: list[dict[str, Any]] = [
        {
            "domain": inferred_domain,
            "filename": name,
            "source": source_label,
            "chunk_index": i,
            **extra_metadata,
        }
        for i in range(len(chunks))
    ]
    ids = [_make_chunk_id(str(path), i) for i in range(len(chunks))]

    if expected_generation is not None:
        from openexecutive.orchestrator.store_access import get_store_generation

        if get_store_generation() != expected_generation:
            logger.warning(
                "ingest_file: skipping write to %s for %s -- store generation "
                "advanced (%d -> %d) between the caller scheduling this ingest "
                "and this task's first turn on the event loop, meaning a "
                "company switch or attachment purge ran in that window (issue #26)",
                collection, name, expected_generation, get_store_generation(),
            )
            return -1

    store.add_documents(texts=texts, metadatas=metadatas, ids=ids, collection=collection)
    return len(chunks)


def ingest_text_sync(
    text: str,
    store: ChromaDBStore,
    *,
    source_name: str,
    domain: str = "general",
    collection: str = ChromaDBStore.COMPANY_COLLECTION,
    extra_metadata: dict[str, Any] | None = None,
) -> int:
    """Synchronous ingest of a raw markdown/text string.

    Mirrors ``ingest_file`` but takes a string — used to persist research
    artifacts and Notion wiki pages into a named collection. ``source_name``
    is the logical identifier for ``filename``/``source`` metadata and the
    chunk-id namespace. ``extra_metadata`` is merged into every chunk.
    Returns the number of chunks written.

    Callers on the API event loop should wrap this in ``asyncio.to_thread``.
    """
    if not text.strip():
        return 0

    chunks = chunk_text(text, chunk_size=512, overlap=50)
    extra = extra_metadata or {}
    metadatas: list[dict[str, Any]] = [
        {
            "domain": domain,
            "filename": source_name,
            "source": source_name,
            "chunk_index": i,
            **extra,
        }
        for i in range(len(chunks))
    ]
    ids = [_make_chunk_id(source_name, i) for i in range(len(chunks))]

    store.add_documents(texts=chunks, metadatas=metadatas, ids=ids, collection=collection)
    return len(chunks)


async def ingest_text(
    text: str,
    store: ChromaDBStore,
    *,
    source_name: str,
    domain: str = "general",
    collection: str = ChromaDBStore.COMPANY_COLLECTION,
    extra_metadata: dict[str, Any] | None = None,
) -> int:
    """Ingest a raw markdown/text string as knowledge (no file on disk).

    Mirrors ``ingest_file`` but takes a string — used to persist the
    executive_research artifact into its own collection. ``source_name``
    is the logical identifier used for both the ``filename``/``source``
    metadata and the chunk-id namespace. ``extra_metadata`` is merged into
    every chunk's metadata (e.g. ``{"type": "recent_research", "created_at": …}``).
    Returns the number of chunks written.
    """
    return ingest_text_sync(
        text,
        store,
        source_name=source_name,
        domain=domain,
        collection=collection,
        extra_metadata=extra_metadata,
    )


async def ingest_builtin_file(
    path: Path,
    store: ChromaDBStore,
    collection: str = ChromaDBStore.BUILTIN_COLLECTION,
    chunk_type: str = "builtin",
    chunk_size: int = 512,
    overlap: int = 50,
) -> int:
    """Index a single built-in markdown file. Caller must delete old chunks first.

    Defaults match the positive-playbook ingest path. Pass
    ``collection=ChromaDBStore.FAILURES_COLLECTION`` (with ``chunk_type='failure_case'``
    and smaller chunks) to ingest a single failure case study — keeps the
    failure CRUD endpoints in lockstep with ``seed_failures``.
    """
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return 0
    domain = infer_domain_from_path(path)
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    metadatas: list[dict[str, Any]] = [
        {
            "domain": domain,
            "filename": path.name,
            "source": str(path),
            "chunk_index": i,
            "type": chunk_type,
        }
        for i in range(len(chunks))
    ]
    ids = [_make_chunk_id(str(path), i) for i in range(len(chunks))]
    store.add_documents(
        texts=chunks,
        metadatas=metadatas,
        ids=ids,
        collection=collection,
    )
    return len(chunks)


def list_company_docs(docs_dir: Path) -> list[dict[str, Any]]:
    if not docs_dir.exists():
        return []
    return [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "modified_at": f.stat().st_mtime,
        }
        for f in sorted(docs_dir.iterdir())
        if f.is_file() and not f.name.startswith(".")
    ]


async def seed_builtin_knowledge(
    store: ChromaDBStore | None = None,
    force: bool = False,
) -> int:
    if store is None:
        from openexecutive.config import get_settings

        settings = get_settings()
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    if not force and store.get_collection_count(ChromaDBStore.BUILTIN_COLLECTION) > 0:
        return 0

    total = 0
    for md_file in BUILTIN_KNOWLEDGE_PATH.rglob("*.md"):
        # Skills live under builtin/skills/ but are indexed into a separate
        # collection by openexecutive.knowledge.skills_index.seed_builtin_skills.
        if any(p in md_file.relative_to(BUILTIN_KNOWLEDGE_PATH).parts for p in ("skills", "failures")):
            continue
        domain = infer_domain_from_path(md_file)
        text = md_file.read_text(encoding="utf-8")
        chunks = chunk_text(text)

        metadatas: list[dict[str, Any]] = [
            {
                "domain": domain,
                "filename": md_file.name,
                "source": str(md_file),
                "chunk_index": i,
                "type": "builtin",
            }
            for i in range(len(chunks))
        ]
        ids = [_make_chunk_id(str(md_file), i) for i in range(len(chunks))]
        store.add_documents(
            texts=chunks,
            metadatas=metadatas,
            ids=ids,
            collection=ChromaDBStore.BUILTIN_COLLECTION,
        )
        total += len(chunks)

    return total


async def seed_failures(
    store: ChromaDBStore | None = None,
    force: bool = False,
) -> int:
    """Index all failure case studies from builtin/failures/<domain>/*.md.

    Idempotent: skipped if the failures collection is already non-empty,
    unless force=True. Uses a smaller chunk size (400 words) to preserve
    the narrative arc of each section (situation/root-cause/lessons).
    """
    if store is None:
        from openexecutive.config import get_settings

        settings = get_settings()
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    if not force and store.get_collection_count(ChromaDBStore.FAILURES_COLLECTION) > 0:
        return 0

    if not FAILURES_KNOWLEDGE_PATH.is_dir():
        logger.warning("failures knowledge path not found, skipping: %s", FAILURES_KNOWLEDGE_PATH)
        return 0

    total = 0
    for md_file in FAILURES_KNOWLEDGE_PATH.rglob("*.md"):
        domain = infer_domain_from_path(md_file)
        text = md_file.read_text(encoding="utf-8")
        if not text.strip():
            continue
        chunks = chunk_text(text, chunk_size=400, overlap=40)
        metadatas: list[dict[str, Any]] = [
            {
                "domain": domain,
                "filename": md_file.name,
                "source": str(md_file),
                "chunk_index": i,
                "type": "failure_case",
            }
            for i in range(len(chunks))
        ]
        ids = [_make_chunk_id(str(md_file), i) for i in range(len(chunks))]
        store.add_documents(
            texts=chunks,
            metadatas=metadatas,
            ids=ids,
            collection=ChromaDBStore.FAILURES_COLLECTION,
        )
        total += len(chunks)

    return total
