"""ChromaDB integration for the skills library.

Each skill is one document (no chunking — frontmatter + 100s of words fit easily).
The search corpus is the concatenation of name, description, and when_to_use;
the body is fetched separately via `load_skill`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openexecutive.knowledge.loader import BUILTIN_KNOWLEDGE_PATH
from openexecutive.knowledge.skills import (
    Skill,
    SkillParseError,
    SkillSource,
    parse_skill_file,
)
from openexecutive.knowledge.store import ChromaDBStore

logger = logging.getLogger(__name__)

SKILLS_COLLECTION = "skills"
BUILTIN_SKILLS_PATH = BUILTIN_KNOWLEDGE_PATH / "skills"


def _company_skills_path() -> Path:
    from openexecutive.config import get_settings

    return get_settings().company_profile_path.parent / "skills"


def _skill_id(name: str, source: SkillSource) -> str:
    return f"skill::{source}::{name}"


def _skill_doc_text(skill: Skill) -> str:
    fm = skill.frontmatter
    return f"{fm.name}\n{fm.description}\n{fm.when_to_use}"


def index_skill(skill: Skill, store: ChromaDBStore) -> None:
    """Upsert a single skill into the skills collection. Idempotent."""
    fm = skill.frontmatter
    store.add_documents(
        texts=[_skill_doc_text(skill)],
        metadatas=[{
            "name": fm.name,
            "category": fm.category,
            "source": skill.source,
            "description": fm.description,
            "when_to_use": fm.when_to_use,
            "filename": Path(skill.path).name,
            "path": skill.path,
        }],
        ids=[_skill_id(fm.name, skill.source)],
        collection=SKILLS_COLLECTION,
    )


def delete_skill_index(name: str, source: SkillSource, store: ChromaDBStore) -> None:
    """Remove a single skill row from the index."""
    store.delete_documents(
        collection=SKILLS_COLLECTION,
        where={"$and": [{"name": name}, {"source": source}]},
    )


def search_skills(
    query: str,
    store: ChromaDBStore,
    n_results: int = 5,
    source_filter: SkillSource | None = None,
    category_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic search across the skill index.

    Returns a list of `{name, category, description, when_to_use, source, score}`
    — no body. The Executive must call `load_skill` to fetch the procedure.
    `category_filter` scopes the search to one `SKILL_CATEGORIES` value (e.g.
    a specialist's own domain) — used by `retriever.retrieve_skills()` so a
    specialist consult never surfaces a skill filed under someone else's
    category.
    """
    if n_results <= 0:
        return []

    col = store._get_or_create_collection(SKILLS_COLLECTION)
    count = col.count()
    if count == 0:
        return []

    conditions: list[dict[str, Any]] = []
    if source_filter:
        conditions.append({"source": source_filter})
    if category_filter:
        conditions.append({"category": category_filter})
    where: dict[str, Any] | None
    if not conditions:
        where = None
    elif len(conditions) == 1:
        where = conditions[0]
    else:
        where = {"$and": conditions}
    query_kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": min(n_results, count),
        "include": ["metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    results = col.query(**query_kwargs)
    hits: list[dict[str, Any]] = []
    if results["metadatas"] and results["metadatas"][0]:
        for meta, dist in zip(
            results["metadatas"][0],
            results["distances"][0],
            strict=False,
        ):
            # Cosine distance -> similarity score for human readability.
            # "score" is clamped/rounded for display; "distance" is the raw
            # value, kept separately so a caller applying a relevance
            # threshold (retriever.retrieve_skills) compares against the
            # real distance rather than reconstructing (and losing
            # precision/clamping) from the rounded score.
            distance = float(dist)
            score = max(0.0, 1.0 - distance)
            hits.append({
                "name": meta.get("name", ""),
                "category": meta.get("category", ""),
                "description": meta.get("description", ""),
                "when_to_use": meta.get("when_to_use", ""),
                "source": meta.get("source", ""),
                "score": round(score, 4),
                "distance": distance,
            })
    return hits


def _iter_skill_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


async def seed_builtin_skills(store: ChromaDBStore | None = None, force: bool = False) -> int:
    """Index every built-in skill on disk. Idempotent: skipped if collection non-empty."""
    if store is None:
        from openexecutive.config import get_settings

        store = ChromaDBStore(persist_directory=get_settings().vector_store_path)

    if not force and store.get_collection_count(SKILLS_COLLECTION) > 0:
        return 0

    count = 0
    for path in _iter_skill_files(BUILTIN_SKILLS_PATH):
        try:
            skill = parse_skill_file(path, source="builtin")
        except SkillParseError as e:
            logger.warning("Skipping malformed skill %s: %s", path, e)
            continue
        index_skill(skill, store)
        count += 1
    return count


def skills_active_for(specialist_name: str, store: ChromaDBStore) -> bool:
    """True iff this specialist's domain has any populated skill content.

    The single shared gate for the specialist-consult skills path — call
    this everywhere that decision needs to be made rather than re-deriving
    it. Keys off `BaseAgent.domain` (the class attribute), NOT
    `retriever.DOMAIN_ALIASES` — those two mappings disagree for several
    specialists (issue #12): `grc`'s alias list is `["governance",
    "compliance"]` and never contains `"security"`, even though
    `GRCAgent.domain == "security"` and that's where its skill content
    actually lives. Using `DOMAIN_ALIASES` here would silently exclude it.

    No caching (benchmarked: an unfiltered full-collection fetch ran
    p50=1.24ms/p95=1.58ms over ~25 docs — negligible next to a
    multi-second specialist LLM call). Uses a `where`-filtered,
    `limit=1` existence check (mirrors `count_skills`'s pattern below)
    rather than materializing every skill's metadata just to compute
    membership — cost still scales with total *categories* queried
    (one call per specialist per turn), not with total skill count.

    Fails closed, never raises: unknown specialist, a registry entry with
    no `domain` attribute (some test doubles), or a lookup error against
    the collection all return `False` rather than propagating — this gate
    must not be able to break a specialist consult, or (since
    `_retrieve_for_call` also calls it) plain `retrieve()` either.
    """
    from openexecutive.orchestrator.router import SPECIALIST_REGISTRY

    agent = SPECIALIST_REGISTRY.get(specialist_name)
    domain = getattr(agent, "domain", None)
    if domain is None:
        return False

    try:
        col = store._get_or_create_collection(SKILLS_COLLECTION)
        result = col.get(where={"category": domain}, limit=1, include=[])
        return len(result.get("ids", [])) > 0
    except Exception:
        logger.exception("skills_active_for: lookup failed for domain %r", domain)
        return False


def count_skills(store: ChromaDBStore, source: SkillSource | None = None) -> int:
    """Count indexed skills, optionally filtered by source."""
    col = store._get_or_create_collection(SKILLS_COLLECTION)
    if source is None:
        return col.count()
    try:
        result = col.get(where={"source": source}, include=[])
        return len(result.get("ids", []))
    except Exception:
        return 0
