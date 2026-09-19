from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple

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


# The "fine" chunk profile (issues #31, #32). Chroma's default embedding model
# (all-MiniLM-L6-v2) embeds only the FIRST 256 TOKENS of its input -- verified:
# two texts that differ only after token 256 embed identically (cosine 1.0).
# Technical prose averages ~1.5-1.6 tokens/word, so the old 512-word chunks
# (median ~700 tokens) were represented by roughly their first third and the
# rest was invisible to vector search. 120 words peaks at 222 tokens on real
# policy docs and 245 on the built-in library (fits with margin); 150 already
# overflows. Measured at the unchanged 0.55 distance gate:
#   company docs (5 policy docs):  recall@3 6/12 -> 9/12, facts reaching the
#     specialist 2/8 -> 6/8, no false positives;
#   built-in library (76 docs, heading questions): late-in-document recall
#     22/60 -> 33/60, early 38/60 -> 40/60, off-topic queries still 0/12.
FINE_CHUNK_WORDS = 120
FINE_CHUNK_OVERLAP = 20

# Above this many words a text falls back to the legacy 512/50 chunking (only
# when the caller didn't ask for a size explicitly). Embedding runs
# synchronously inside ingest_file at ~14ms/chunk, and the fine size means ~4.6x
# more chunks: fine for a normal document (a 100-page policy is ~6.8s), but an
# extracted 50MB text file (~8M words) would freeze the whole process for ~19
# minutes instead of ~4. Past this point the doc is not a policy anyone
# retrieves passages from by fine-grained match anyway, and the fallback keeps a
# single document's worst case exactly what it was before -- no content is
# dropped and no new failure mode is introduced. 60,000 words is ~120 pages,
# i.e. <=~8s of fine chunking. The bound is PER DOCUMENT: a loop over many
# sub-limit docs (a fixture load, a client-slot rebuild) still costs ~4.6x what
# it did, N x ~8s instead of N x ~1.8s for N near-limit docs.
FINE_CHUNK_MAX_WORDS = 60_000

# The pre-#31 chunking, kept where a larger chunk is the right trade.
LEGACY_CHUNK_WORDS = 512
LEGACY_CHUNK_OVERLAP = 50

# Attachments keep the legacy chunking on purpose. Chunk count (and so the
# synchronous, event-loop-blocking embedding work inside ingest_file) scales
# ~4.3x with the smaller size -- fine for an admin-curated /documents upload
# behind the shared secret, but attachments are accepted from any rostered
# sender, where that multiplier is the wrong trade.
ATTACHMENT_CHUNK_WORDS = LEGACY_CHUNK_WORDS
ATTACHMENT_CHUNK_OVERLAP = LEGACY_CHUNK_OVERLAP

# Written into the metadata of built-in and failure-case chunks so startup can
# tell a collection seeded with an older profile and re-chunk it (issue #32).
# Derived from the sizes, so changing them re-seeds automatically.
CHUNKING_VERSION = f"fine-{FINE_CHUNK_WORDS}-{FINE_CHUNK_OVERLAP}"


def default_chunking(text: str) -> tuple[int, int]:
    """``(chunk_words, overlap)`` for a text whose caller didn't pick a size:
    the fine profile, or the legacy one past ``FINE_CHUNK_MAX_WORDS``."""
    # A text of W words has at least 2W-1 characters, so anything up to
    # 2*MAX characters can't exceed MAX words: skip the full split (which is
    # ~1s and hundreds of MB of transient list on a 40MB string) for the
    # overwhelming majority of texts.
    if (len(text) + 1) // 2 > FINE_CHUNK_MAX_WORDS and len(text.split()) > FINE_CHUNK_MAX_WORDS:
        return LEGACY_CHUNK_WORDS, LEGACY_CHUNK_OVERLAP
    return FINE_CHUNK_WORDS, FINE_CHUNK_OVERLAP


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    if chunk_size < 1 or not 0 <= overlap < chunk_size:
        # overlap >= chunk_size would never advance `start` (infinite loop).
        raise ValueError(f"need chunk_size >= 1 and 0 <= overlap < chunk_size, got {chunk_size}/{overlap}")
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


def infer_domain_from_path(path: Path, root: Path | None = None) -> str:
    """The domain a file's folder names imply, else "general" (visible to every specialist).

    Only folders that are part of the *corpus layout* count. With ``root`` (the
    corpus directory the file was found under) that is the folders between
    ``root`` and the file, so ``builtin/security/x.md`` is ``security`` and
    ``builtin/failures/legal/x.md`` is ``legal``. Without ``root`` -- or when the
    file isn't under it -- only the file's immediate parent folder is looked at.
    Never the rest of the absolute path (issue #35): an install, checkout or
    home directory that happens to sit under a folder called ``legal`` or
    ``security`` used to tag EVERY document with that domain, hiding company
    docs from the other specialists.
    """
    folders: tuple[str, ...] = (path.parent.name,)
    if root is not None and path.is_relative_to(root):
        folders = path.relative_to(root).parts[:-1]
    for folder in folders:
        domain = DOMAIN_MAP.get(folder.lower())
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
    chunk_words: int | None = None,
    chunk_overlap: int | None = None,
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

    ``chunk_words`` / ``chunk_overlap`` (issue #31): when omitted, company
    chunking applies -- ``FINE_CHUNK_WORDS`` / ``FINE_CHUNK_OVERLAP``,
    sized so a whole chunk fits the embedding model's 256-token window (see
    those constants) -- except that a doc over
    ``FINE_CHUNK_MAX_WORDS`` falls back to the previous 512/50 so
    the synchronous embedding work can't grow ~4.6x for a huge file. An
    explicit value is always used as given. The attachment path passes
    ``ATTACHMENT_CHUNK_*`` to keep its old chunking. Docs ingested before this
    change keep their old 512-word chunks until re-ingested (re-upload, or
    reload the fixture).
    """
    text = extract_text_from_file(path)
    declared_domain: str | None = None
    if honor_front_matter and domain is None and path.suffix.lower() == ".md":
        declared_domain, text = _split_front_matter_domain(text, path.name)
    if not text.strip():
        return 0

    inferred_domain = domain or declared_domain or infer_domain_from_path(path)
    if chunk_words is None and chunk_overlap is None:
        chunk_words, chunk_overlap = default_chunking(text)
        if chunk_words == LEGACY_CHUNK_WORDS:
            logger.info(
                "ingest_file: %s is over %d words -- using %d-word chunks instead of %d "
                "to bound synchronous embedding time",
                _sanitize_display_name(display_name or path.name),
                FINE_CHUNK_MAX_WORDS, LEGACY_CHUNK_WORDS, FINE_CHUNK_WORDS,
            )
    elif chunk_words is None or chunk_overlap is None:
        raise ValueError("pass chunk_words and chunk_overlap together, or neither")
    chunks = chunk_text(text, chunk_size=chunk_words, overlap=chunk_overlap)

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

    chunks = chunk_text(text, *default_chunking(text))
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
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> int:
    """Index a single built-in markdown file. Caller must delete old chunks first.

    Chunking defaults to ``default_chunking`` (the fine profile), the same as
    ``seed_builtin_knowledge`` / ``seed_failures``. Pass
    ``collection=ChromaDBStore.FAILURES_COLLECTION`` (with ``chunk_type='failure_case'``)
    to ingest a single failure case study — keeps the failure CRUD endpoints in
    lockstep with ``seed_failures``. Every chunk carries the current
    ``CHUNKING_VERSION`` so startup can tell a collection seeded before issue #32.
    """
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return 0
    domain = infer_domain_from_path(path, BUILTIN_KNOWLEDGE_PATH)
    if chunk_size is None and overlap is None:
        chunk_size, overlap = default_chunking(text)
    elif chunk_size is None or overlap is None:
        raise ValueError("pass chunk_size and overlap together, or neither")
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    metadatas: list[dict[str, Any]] = [
        {
            "domain": domain,
            "filename": path.name,
            "source": str(path),
            "chunk_index": i,
            "type": chunk_type,
            "chunking": CHUNKING_VERSION,
        }
        for i in range(len(chunks))
    ]
    ids = [_make_chunk_id(str(path), i) for i in range(len(chunks))]
    # Deliberately synchronous (no await): the CRUD routes delete this file's
    # old rows, write it, then call this, and staying on the event loop keeps
    # that sequence atomic against a concurrent PUT/DELETE of the same doc.
    # Moving it to a thread would let an in-flight write resurrect rows a
    # DELETE just removed. Cost: a large body blocks the loop for its embedding
    # time (admin-only routes; see the FINE_CHUNK_MAX_WORDS bound).
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


_CHUNKING_PENDING = "pending"


class _Indexed(NamedTuple):
    total: int
    ids: set[str]
    read: set[str]     # sources successfully read (blank files included)
    indexed: set[str]  # sources that produced at least one chunk


def _index_seed_files(
    store: ChromaDBStore,
    files: list[Path],
    root: Path,
    collection: str,
    chunk_type: str,
) -> _Indexed:
    """Chunk and upsert each markdown file.

    A file's rows are written with a provisional ``chunking`` marker and only
    stamped ``CHUNKING_VERSION`` after every batch landed (``add_documents``
    upserts in batches of 100), so a crash between batches can't leave a
    truncated file that reads as current.
    """
    total = 0
    new_ids: set[str] = set()
    read_sources: set[str] = set()
    indexed_sources: set[str] = set()
    for md_file in files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # One bad file must not abort startup (or a half-done migration).
            # Its source is left out of read_sources, so its existing rows are
            # neither replaced nor deleted, and it is retried next boot.
            logger.warning("skipping unreadable knowledge file: %s", _sanitize_display_name(md_file.name))
            continue
        read_sources.add(str(md_file))
        if not text.strip():
            continue
        chunks = chunk_text(text, *default_chunking(text))
        metadatas: list[dict[str, Any]] = [
            {
                "domain": infer_domain_from_path(md_file, root),
                "filename": md_file.name,
                "source": str(md_file),
                "chunk_index": i,
                "type": chunk_type,
                "chunking": _CHUNKING_PENDING,
            }
            for i in range(len(chunks))
        ]
        ids = [_make_chunk_id(str(md_file), i) for i in range(len(chunks))]
        store.add_documents(texts=chunks, metadatas=metadatas, ids=ids, collection=collection)
        store.stamp_chunking(collection, ids, CHUNKING_VERSION)
        total += len(chunks)
        new_ids.update(ids)
        indexed_sources.add(str(md_file))
    return _Indexed(total, new_ids, read_sources, indexed_sources)


def _manifest_keys(files: list[Path], root: Path) -> dict[Path, str]:
    """Stable per-file keys: the path relative to the corpus root, so neither an
    install that moved on disk nor two files that share a name collide."""
    return {f: f.relative_to(root).as_posix() for f in files}


def _seed_or_migrate(
    store: ChromaDBStore,
    files: list[Path],
    root: Path,
    collection: str,
    chunk_type: str,
    force: bool,
) -> int:
    """Seed a collection: fresh, incrementally, or one-time re-chunked (issue #32).

    * **Empty collection or ``force=True``:** every shipped file is indexed.
    * **Populated collection:** only *targets* are indexed -- files whose rows
      are not at the current ``CHUNKING_VERSION`` (a store seeded before #32
      holds 400-512-word chunks, most longer than the embedding window), and
      files a previous startup never saw. "Never saw" is a manifest of shipped
      file keys (path relative to the corpus root) kept in
      ``seed_manifest_<collection>.json`` next to the store, NOT "has no rows":
      a doc an admin deleted through the API must not come back at the next
      restart just because the image still ships the file. The first startup
      after manifests appeared bootstraps it from the rows already present (a
      shipped doc deleted before that point is re-added once). The manifest only
      grows; a missing or short corpus (bad mount) never prunes it.

    Re-chunking is upsert-then-delete, never delete-then-upsert: new chunks are
    written first (same ids, so they overwrite), and only then are the leftover
    old rows of the files actually read removed. Rows are stamped current per
    file only after all of its batches are written, so a crash at any point
    leaves rows that still read as stale and the next startup finishes the job
    for just those files. Only rows of ``chunk_type`` whose ``source`` is a file
    being reseeded are replaced: external OER rows (other ``type``) share the
    built-in collection, and rows indexed through the API from a file no longer
    on this disk have nothing to be re-chunked from -- both are left as they
    are. (If the install path changed since seeding, no row's ``source``
    matches and nothing re-chunks; the log line says how many rows were left.)

    On a populated collection a failure is logged and startup carries on (the
    same files are retried next boot); on a fresh seed or ``force`` it
    propagates, as it always did. ``force=True`` also drops the leftover rows of
    the files it re-read (a file that shrank).

    The pass is synchronous embedding inside the app lifespan (~15s for the
    whole shipped corpus on a laptop, longer on a small VM); the API isn't
    serving until it returns.
    """
    keys = _manifest_keys(files, root)
    manifest = store.read_seed_manifest(collection)
    stale_rows: dict[str, str | None] = {}
    targets = files
    populated = not force and store.get_collection_count(collection) > 0
    if force:
        # "" matches no marker, so this lists every row of the type: after the
        # upsert, any of a re-read file's rows that weren't rewritten (the file
        # shrank) are removed, same as the automatic path.
        stale_rows = store.stale_chunk_sources(collection, {"type": chunk_type}, "")
    elif populated:
        stale_rows = store.stale_chunk_sources(collection, {"type": chunk_type}, CHUNKING_VERSION)
        by_source = {str(f): f for f in files}
        stale_files = {by_source[s] for s in stale_rows.values() if s in by_source}
        if manifest is None:
            present = store.indexed_files(collection, {"type": chunk_type})
            if present is not None:  # None = unreadable: learn nothing, add nothing
                manifest = {key for f, key in keys.items() if (infer_domain_from_path(f, root), f.name) in present}
        unseen = {f for f in files if manifest is not None and keys[f] not in manifest}
        targets = [f for f in files if f in stale_files or f in unseen]
        if stale_rows:
            logger.info(
                "%s: %d %s rows are not at %s; %d have a source file on disk and will be re-chunked "
                "(one-time, issue #32), %d are left as-is",
                collection, len(stale_rows), chunk_type, CHUNKING_VERSION,
                sum(1 for s in stale_rows.values() if s in by_source), sum(1 for s in stale_rows.values() if s not in by_source),
            )
        if not targets:
            if manifest is not None and store.read_seed_manifest(collection) is None:
                store.write_seed_manifest(collection, manifest)
            return 0

    try:
        result = _index_seed_files(store, targets, root, collection, chunk_type)
    except Exception:
        if not populated:
            raise
        logger.exception("%s: indexing %d %s file(s) failed; startup continues, retrying next boot",
                         collection, len(targets), chunk_type)
        return 0

    leftovers = [
        row_id for row_id, source in stale_rows.items()
        if source in result.read and row_id not in result.ids
    ]
    if leftovers:
        store.delete_ids(collection, leftovers)
    if not populated or manifest is not None:
        known = (manifest or set()) | {keys[f] for f in targets if str(f) in result.indexed}
        if known and known != manifest:
            store.write_seed_manifest(collection, known)
    if populated and result.total:
        logger.info("%s: indexed %d chunks from %d %s file(s)", collection, result.total, len(targets), chunk_type)
    return result.total


async def seed_builtin_knowledge(
    store: ChromaDBStore | None = None,
    force: bool = False,
) -> int:
    if store is None:
        from openexecutive.config import get_settings

        settings = get_settings()
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    # Skills live under builtin/skills/ but are indexed into a separate
    # collection by openexecutive.knowledge.skills_index.seed_builtin_skills.
    files = [
        f for f in BUILTIN_KNOWLEDGE_PATH.rglob("*.md")
        if not any(p in f.relative_to(BUILTIN_KNOWLEDGE_PATH).parts for p in ("skills", "failures"))
    ]
    return _seed_or_migrate(store, files, BUILTIN_KNOWLEDGE_PATH, ChromaDBStore.BUILTIN_COLLECTION, "builtin", force)


async def seed_failures(
    store: ChromaDBStore | None = None,
    force: bool = False,
) -> int:
    """Index all failure case studies from builtin/failures/<domain>/*.md.

    Idempotent: a populated failures collection is left alone (apart from
    indexing newly shipped files) unless force=True -- and except that a collection chunked before issue #32
    (400-word chunks, most longer than the embedding window) is re-chunked once
    to the current profile (see ``_seed_or_migrate``). Uses the same fine
    chunking as everything else; ``ingest_builtin_file`` (the failure CRUD
    routes) does too, so a PUT produces the same chunks as seed time.
    """
    if store is None:
        from openexecutive.config import get_settings

        settings = get_settings()
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    if not FAILURES_KNOWLEDGE_PATH.is_dir():
        logger.warning("failures knowledge path not found, skipping: %s", FAILURES_KNOWLEDGE_PATH)
        return 0
    files = list(FAILURES_KNOWLEDGE_PATH.rglob("*.md"))
    return _seed_or_migrate(store, files, FAILURES_KNOWLEDGE_PATH, ChromaDBStore.FAILURES_COLLECTION, "failure_case", force)
