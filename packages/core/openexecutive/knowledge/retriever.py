from __future__ import annotations

import logging
import re
from typing import Any

from openexecutive.audit import get_active_ids
from openexecutive.audit import log_event as _audit_log
from openexecutive.knowledge.review_store import (
    PRIORITY_ORDER,
    ContentType,
    Priority,
    ReviewStore,
)
from openexecutive.knowledge.store import ChromaDBStore

logger = logging.getLogger(__name__)

# Cosine distance threshold for the main retrieve() path. Hits with a
# distance > this are dropped before the top-K slice. Mirrors the value
# already used by retrieve_failures() — weak matches are noise that
# poisons grounding (e.g. a "Hi" greeting pulling a GitLab handbook
# chunk because it happens to be the closest seeded knowledge).
_DISTANCE_THRESHOLD = 0.55

# Minimum character length for RAG to fire. Below this we treat the
# message as a greeting / acknowledgement ("Hi", "ok") and skip the
# vector store entirely. Char count (not token count) because `\w+`
# matches a CJK sentence as a single token, which would incorrectly
# bypass RAG for meaningful Chinese/Japanese queries. Threshold sits
# at 3 so 3-letter business acronyms ("ROI", "CFO", "P&L") still fire.
_MIN_QUERY_CHARS = 3


_ATX_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")


def _neutralize_rag_headings(text: str) -> str:
    """Strip ATX headings so untrusted wiki text cannot spoof RAG section labels."""
    return _ATX_HEADING.sub("", text)


def _format_untrusted_wiki(text: str) -> str:
    """Prefix every line so wiki prose cannot impersonate citation markers."""
    cleaned = _neutralize_rag_headings(text)
    return "\n".join(f"· {line}" for line in cleaned.splitlines())


def _passes_threshold(
    row: dict[str, Any], threshold: float = _DISTANCE_THRESHOLD
) -> bool:
    """True iff the Chroma row's cosine distance is within the relevance gate.

    Treats a missing/None distance as out-of-bounds (we don't surface chunks
    of unknown relevance). Uses an explicit None check rather than `... or
    1.0` because `0.0 or 1.0 == 1.0` would falsy-drop the strongest possible
    match — Chroma returns 0.0 for a verbatim hit. ``threshold`` defaults to
    the module constant but callers pass the settings-configured value.
    """
    distance = row.get("distance")
    if distance is None:
        return False
    return distance <= threshold


def _default_review_store() -> ReviewStore:
    from openexecutive.memory.episodic import DB_PATH

    return ReviewStore(db_path=DB_PATH)


def _dedupe_by_text(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate-text hits from a Chroma result list.

    Multi-domain OER sources fan each chunk out to one row per declared
    domain. A specialist query that filters by domain naturally gets one
    row per chunk, but an unfiltered call (e.g. the Executive's global
    retrieve) could see the same passage 2-5x. Preserve order so the most
    semantically relevant copy wins.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in results:
        if r["text"] in seen:
            continue
        seen.add(r["text"])
        out.append(r)
    return out


def _emit_retrieval_audit(
    *,
    query: str,
    domain_filter: list[str] | None,
    specialist_name: str | None,
    builtin_results: list[dict[str, Any]],
    company_results: list[dict[str, Any]],
    annotation_count: int,
    collection: str,
) -> None:
    """Fire-and-forget audit emit for a retrieval pass.

    Reads (session_id, turn_id) from the audit context vars set by the
    Executive at turn entry; emits None for both when called outside a
    turn (CLI, ad-hoc workflows) so the row is still captured but won't
    cluster into a session timeline. log_event already swallows.
    """
    session_id, turn_id = get_active_ids()

    def _chunks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "source": r.get("metadata", {}).get("filename"),
                "domain": r.get("metadata", {}).get("domain"),
                "distance": r.get("distance"),
                # First 400 chars is enough to recognise the passage in the
                # UI without bloating audit rows; full text lives in Chroma.
                "text_preview": (r.get("text") or "")[:400],
            }
            for r in rows
        ]

    total = len(builtin_results) + len(company_results)
    domain_str = ",".join(domain_filter) if domain_filter else "*"
    _audit_log(
        "knowledge_retrieval",
        f"retrieve({domain_str}) → {total} chunks: {query[:140]}",
        session_id=session_id,
        turn_id=turn_id,
        actor=specialist_name or "executive",
        details={
            "query": query[:300],
            "collection": collection,
            "domain_filter": domain_filter,
            "specialist": specialist_name,
            "builtin_count": len(builtin_results),
            "company_count": len(company_results),
            "annotation_count": annotation_count,
        },
        full={
            "query": query,
            "domain_filter": domain_filter,
            "specialist": specialist_name,
            "builtin_chunks": _chunks(builtin_results),
            "company_chunks": _chunks(company_results),
        },
    )


DOMAIN_ALIASES: dict[str, list[str]] = {
    "cso": ["strategy"],
    "cfo": ["finance"],
    "chro": ["hr"],
    "gc": ["legal"],
    "coo": ["operations"],
    "cmo": ["marketing"],
    "cpo": ["product", "strategy"],
    "ciso": ["security", "governance"],
    "cyberops": ["security"],
    "grc": ["governance", "compliance"],
    "board_comms": ["board", "finance"],
    # The talent specialist reuses the existing HR + strategy knowledge
    # domains until a dedicated `talent` knowledge corpus is seeded (Phase 2).
    "talent": ["hr", "strategy"],
}


def retrieve(
    query: str,
    domain_filter: list[str] | None = None,
    specialist_name: str | None = None,
    n_builtin: int | None = None,
    n_company: int | None = None,
    store: ChromaDBStore | None = None,
    review_store: ReviewStore | None = None,
    distance_threshold: float | None = None,
) -> str:
    effective_domains = domain_filter
    if effective_domains is None and specialist_name:
        effective_domains = DOMAIN_ALIASES.get(specialist_name)

    # Short-message bypass: greetings and acknowledgements never benefit
    # from semantic retrieval and reliably surface noise. Skip the ChromaDB
    # roundtrip entirely, but still emit audit so the flow chart records
    # "we considered RAG and gated it out". Longer-but-tangential queries
    # are caught by the distance threshold below, not here.
    if len(query.strip()) < _MIN_QUERY_CHARS:
        _emit_retrieval_audit(
            query=query,
            domain_filter=effective_domains,
            specialist_name=specialist_name,
            builtin_results=[],
            company_results=[],
            annotation_count=0,
            collection="builtin+company (bypassed: short query)",
        )
        return ""

    # Resolve tunable retrieval params from settings when not explicitly
    # passed. Callers that pass values (e.g. the report workflows) keep
    # them; the chat path leaves them None and inherits the configured
    # defaults. get_settings() is uncached, so KNOWLEDGE_* env overrides
    # take effect on the next call — this is the lever the RAG ablation
    # harness toggles (KNOWLEDGE_BUILTIN_N_RESULTS=0 disables builtin RAG).
    from openexecutive.config import get_settings

    settings = get_settings()
    if n_builtin is None:
        n_builtin = settings.knowledge_builtin_n_results
    if n_company is None:
        n_company = settings.knowledge_company_n_results
    if distance_threshold is None:
        distance_threshold = settings.knowledge_distance_threshold

    if store is None:
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    rs = review_store or _default_review_store()
    rejected_builtin = rs.get_rejected_filenames(ContentType.BUILTIN)
    rejected_external = rs.get_rejected_source_ids()
    priority_map = rs.get_priority_map(ContentType.BUILTIN)

    # Over-fetch slightly so post-query text dedup (multi-domain chunks share
    # the same text across rows) still leaves us with the requested count.
    # n_builtin <= 0 disables builtin-knowledge retrieval entirely (the
    # lever the RAG ablation harness flips). Skip the query rather than
    # asking Chroma for 0 results.
    if n_builtin > 0:
        raw_builtin = _dedupe_by_text(
            store.query(
                query_text=query,
                collection=ChromaDBStore.BUILTIN_COLLECTION,
                domain_filter=effective_domains,
                n_results=n_builtin * 3,
            )
        )

        # Filter out rejected files and rejected OER sources, drop weak
        # matches, then sort by SME priority.
        filtered_builtin = [
            r
            for r in raw_builtin
            if r["metadata"].get("filename") not in rejected_builtin
            and r["metadata"].get("source_id") not in rejected_external
            and _passes_threshold(r, distance_threshold)
        ]
        filtered_builtin.sort(
            key=lambda r: PRIORITY_ORDER.get(
                priority_map.get(r["metadata"].get("filename", ""), Priority.NORMAL.value),
                1,
            )
        )
        builtin_results = filtered_builtin[:n_builtin]
    else:
        builtin_results = []

    if n_company > 0:
        raw_company = store.query(
            query_text=query,
            collection=ChromaDBStore.COMPANY_COLLECTION,
            domain_filter=effective_domains,
            n_results=n_company,
        )
        company_results = [
            r for r in raw_company if _passes_threshold(r, distance_threshold)
        ]
    else:
        company_results = []

    # Synced Notion wiki — isolated from COMPANY because a Notion share is
    # multi-writer and unreviewed. Ranked below curated company docs and
    # labelled so specialists do not treat it as policy.
    raw_notion = store.query(
        query_text=query,
        collection=ChromaDBStore.NOTION_COLLECTION,
        domain_filter=effective_domains,
        n_results=3,
    )
    notion_results = [
        r for r in raw_notion if _passes_threshold(r, distance_threshold)
    ]

    # Recent research artifacts — kept in a separate collection and ranked
    # BELOW curated company docs. These are unvetted, web-sourced summaries
    # from executive_research runs, so they are clearly labelled as such and
    # never blended into the company-documents section above.
    raw_research = store.query(
        query_text=query,
        collection=ChromaDBStore.RESEARCH_COLLECTION,
        domain_filter=None,  # research is cross-domain; never domain-scoped
        n_results=2,
    )
    research_results = [
        r for r in raw_research if _passes_threshold(r, distance_threshold)
    ]

    active_annotations = rs.list_annotations(domains=effective_domains, active_only=True)

    # Audit emit — always fire, even on empty results, so the flow chart
    # can show "we asked but found nothing" rather than silently omitting
    # the retrieval step. Fire-and-forget; never blocks/breaks the caller.
    _emit_retrieval_audit(
        query=query,
        domain_filter=effective_domains,
        specialist_name=specialist_name,
        builtin_results=builtin_results,
        company_results=company_results,
        annotation_count=len(active_annotations),
        collection="builtin+company",
    )

    if (
        not builtin_results
        and not company_results
        and not notion_results
        and not research_results
        and not active_annotations
    ):
        return ""

    parts: list[str] = []

    if company_results:
        parts.append("### From your company documents:")
        for r in company_results:
            filename = r["metadata"].get("filename", "unknown")
            parts.append(f"[{filename}] {r['text']}")

    if notion_results:
        parts.append(
            "### Synced Notion wiki (unreviewed, multi-writer — weigh below "
            "curated company documents):"
        )
        for r in notion_results:
            filename = r["metadata"].get("filename", "unknown")
            parts.append(
                f"[notion:{filename}]\n{_format_untrusted_wiki(r['text'])}"
            )

    if research_results:
        parts.append(
            "### Recent research (unverified, web-sourced — weigh below "
            "company documents):"
        )
        for r in research_results:
            created = r["metadata"].get("created_at", "")
            when = f" — {created}" if created else ""
            parts.append(f"[recent research{when}] {r['text']}")

    if builtin_results:
        parts.append("### From executive knowledge base:")
        for r in builtin_results:
            filename = r["metadata"].get("filename", "unknown")
            prio = priority_map.get(filename, Priority.NORMAL.value)
            prefix = "[verified - priority source] " if prio == Priority.HIGH.value else ""
            parts.append(f"[{filename}] {prefix}{r['text']}")

    if active_annotations:
        parts.append("### SME corrections and context:")
        for ann in active_annotations:
            parts.append(f"[SME annotation] {ann.correction}")

    return "\n\n".join(parts)


def retrieve_failures(
    query: str,
    domain_filter: list[str] | None = None,
    specialist_name: str | None = None,
    n_results: int = 2,
    store: ChromaDBStore | None = None,
) -> str:
    """Query the failure_cases collection and return formatted context.

    Returns an empty string if no result clears the distance threshold —
    tangential failure stories are noise, so we prefer surfacing nothing
    over surfacing a poor match.
    """
    from openexecutive.config import get_settings

    settings = get_settings()
    if store is None:
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    effective_domains = domain_filter
    if effective_domains is None and specialist_name:
        effective_domains = DOMAIN_ALIASES.get(specialist_name)

    raw = store.query(
        query_text=query,
        collection=ChromaDBStore.FAILURES_COLLECTION,
        domain_filter=effective_domains,
        n_results=n_results * 2,
    )

    # Cosine distance threshold (configurable via KNOWLEDGE_DISTANCE_THRESHOLD):
    # a larger distance means the match is too weak to be useful.
    threshold = settings.knowledge_distance_threshold
    filtered = _dedupe_by_text([r for r in raw if r["distance"] <= threshold])
    results = filtered[:n_results]

    # Audit emit (failure cases collection). Fire even when empty so the
    # timeline shows we considered failure stories and rejected them.
    _emit_retrieval_audit(
        query=query,
        domain_filter=effective_domains,
        specialist_name=specialist_name,
        builtin_results=results,
        company_results=[],
        annotation_count=0,
        collection="failure_cases",
    )

    if not results:
        return ""

    parts = ["### Relevant failure cases:"]
    for r in results:
        filename = r["metadata"].get("filename", "unknown")
        parts.append(f"[{filename}] {r['text']}")
    return "\n\n".join(parts)


def retrieve_skills(
    query: str,
    specialist_name: str | None = None,
    n_results: int = 1,
    store: ChromaDBStore | None = None,
    distance_threshold: float | None = None,
) -> str:
    """Domain-gated, single-best-match skills-library lookup for one specialist call.

    Returns "" with zero ChromaDB queries when the specialist's domain has no
    populated skill content (see `skills_index.skills_active_for` — the one
    shared gate; do not re-derive this check here or anywhere else) or when
    `query` is too short to benefit from semantic search (same
    `_MIN_QUERY_CHARS` bypass `retrieve()` applies).

    Skills are indexed shallow (name+description+when_to_use only, see
    `skills_index._skill_doc_text`), so a hit clearing the relevance
    threshold triggers a second step, `skills_repo.get_skill()`, to load
    the full body before injection -- mirrors the Executive's own
    `search_skills` -> `load_skill` pair, collapsed into one deterministic
    call. `n_results=1`: a whole skill body runs ~1.5-2x a single BUILTIN
    chunk's size, so capping at one keeps the combined per-consult context
    budget close to today's levels (see `_retrieve_for_call`'s matching
    `n_builtin` reduction).

    This is an optional enrichment, not core retrieval: any exception from
    the gate or the search itself is treated as "nothing found," never
    propagated -- a skills-collection problem must not be able to break a
    specialist consult, and (via the shared `skills_active_for` gate) must
    not be able to break plain `retrieve()` either.

    `source == "company"` skill bodies are user-created and get the same
    treatment `retrieve()` already applies to synced Notion wiki text:
    `_format_untrusted_wiki()` (ATX headers stripped AND every line
    prefixed, not just headers stripped) plus an explicit unverified
    label. The per-line prefix matters as much as the heading strip here —
    without it a hit body can still forge a closing `</relevant_skill>`
    followed by a fake `<relevant_knowledge>` block carrying a
    `[verified - priority source]` citation tag, escaping its own
    container and outranking the label meant to demote it. A company
    skill can be created by a prompt-injected Executive acting on
    attacker-controlled content (Notion, recent_research) and later
    replayed verbatim into a *different* specialist's trusted context.
    `builtin` skills are shipped with the repo and read-only at runtime,
    so they stay at the same trust tier as curated BUILTIN knowledge.
    """
    from openexecutive.config import get_settings
    from openexecutive.knowledge import skills_repo
    from openexecutive.knowledge.skills_index import search_skills, skills_active_for

    if specialist_name is None:
        return ""

    # Resolved once, up front: cheap dict lookup (no exception risk), and
    # every _emit() call below -- including the early short-query/gate-closed
    # bypasses -- needs it for domain_filter. `agent` may legitimately be
    # None for an unknown specialist_name; skills_active_for handles that
    # (returns False) rather than this function re-deriving the check.
    from openexecutive.orchestrator.router import SPECIALIST_REGISTRY

    agent = SPECIALIST_REGISTRY.get(specialist_name)
    settings = get_settings()

    def _emit(result_row: dict[str, Any] | None) -> None:
        _emit_retrieval_audit(
            query=query,
            domain_filter=[agent.domain] if agent is not None else None,
            specialist_name=specialist_name,
            builtin_results=[result_row] if result_row else [],
            company_results=[],
            annotation_count=0,
            collection="skills",
        )

    if len(query.strip()) < _MIN_QUERY_CHARS:
        _emit(None)
        return ""

    if store is None:
        store = ChromaDBStore(persist_directory=settings.vector_store_path)

    try:
        active = skills_active_for(specialist_name, store)
    except Exception:  # noqa: BLE001 - optional enrichment must never break a consult
        logger.exception("retrieve_skills: skills_active_for failed for %r", specialist_name)
        active = False
    if not active or agent is None:
        _emit(None)
        return ""

    threshold = (
        distance_threshold if distance_threshold is not None else settings.knowledge_distance_threshold
    )

    try:
        hits = search_skills(query, store, n_results=n_results, category_filter=agent.domain)
    except Exception:  # noqa: BLE001 - optional enrichment must never break a consult
        logger.exception("retrieve_skills: search_skills failed for specialist %r", specialist_name)
        hits = []

    if not hits:
        _emit(None)
        return ""

    best = hits[0]
    distance = best["distance"]
    if distance > threshold:
        _emit(None)
        return ""

    try:
        skill = skills_repo.get_skill(best["name"])
    except Exception as exc:  # noqa: BLE001 - never let a malformed/missing skill break a consult
        # Log the real exception (may embed an absolute server path, e.g.
        # SkillParseError) server-side only -- the audit row (readable via
        # the /audit/logs/{id} API) gets just the exception class and skill
        # name, distinguishable from "no hits"/"below threshold" (both
        # emit None, i.e. an empty chunk list) without leaking path detail.
        logger.warning("retrieve_skills: failed to load matched skill %r: %s", best["name"], exc)
        _emit({
            "metadata": {"filename": best["name"], "domain": agent.domain},
            "distance": distance,
            "text": f"[skill matched but failed to load: {type(exc).__name__}]",
        })
        return ""

    fm = skill.frontmatter
    if skill.source == "company":
        # User-created content, same trust tier and treatment as synced
        # Notion wiki text -- see docstring.
        safe_description = _format_untrusted_wiki(fm.description)
        safe_when_to_use = _format_untrusted_wiki(fm.when_to_use)
        safe_body = _format_untrusted_wiki(skill.body)
        body_text = (
            f"**Skill**: {fm.name}\n\n"
            f"**Category**: {fm.category}  \n"
            f"**Source**: company (user-created — unverified, weigh below builtin skills)\n\n"
            f"**Description**: {safe_description}\n\n"
            f"**When to use**: {safe_when_to_use}\n\n"
            f"---\n\n{safe_body}"
        )
    else:
        body_text = (
            f"# {fm.name}\n\n"
            f"**Category**: {fm.category}  \n"
            f"**Source**: {skill.source}\n\n"
            f"**Description**: {fm.description}\n\n"
            f"**When to use**: {fm.when_to_use}\n\n"
            f"---\n\n{skill.body}"
        )

    _emit({
        "metadata": {"filename": fm.name, "domain": agent.domain},
        "distance": distance,
        "text": body_text,
    })

    return f"### Relevant skill:\n\n{body_text}"
