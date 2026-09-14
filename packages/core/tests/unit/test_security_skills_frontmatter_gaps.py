"""Regression guard for issue #20's frontmatter-indexing gap, tested against
the REAL embedding index — not a synthetic store.

7 of 10 files in knowledge/builtin/skills/security/ shipped with the same
defect (#14, #20): a body section covering a specific technique or scenario
that description/when_to_use never named, so skills_index._skill_doc_text()
(name + description + when_to_use — the entire search corpus) never
embedded it. Read-through review alone missed these; only testing against
the real index caught them (#20's own text: "well-aligned" on read-through,
broken on direct testing, twice) -- and the fix ITSELF nearly repeated the
mistake: the first rewrite of security-investment-prioritization.md's
frontmatter cleared the #20 target query but pushed five other, previously-
passing queries ("respond to a budget challenge", "justify a strategic
hire", ...) over the relevance gate. Adversarial review caught it with the
same technique: test more than just the one query you're trying to fix.

This test locks in both: the two target queries #20 named, AND a broader
set of queries the fix must not silently break. A future edit that
quietly weakens either file's frontmatter -- for either reason -- fails CI
instead of shipping silently, the way the original gap did.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from openexecutive.config import get_settings
from openexecutive.knowledge.skills_index import index_skill, search_skills
from openexecutive.knowledge.skills_repo import list_skills
from openexecutive.knowledge.store import ChromaDBStore

# The exact trigger queries from issue #20's own investigation -- these must
# pass with real margin, not just barely clear the gate.
_TARGET_CASES = [
    (
        "incident-escalation-framework",
        "We think this incident might touch regulated data and could "
        "trigger a statutory breach-notification clock -- does this need "
        "to go to the board and legal right now?",
    ),
    (
        "security-investment-prioritization",
        "I need to build a budget justification for a new EDR platform "
        "tied to a specific gap our red team found.",
    ),
]

# Queries adversarial review found the first fix draft had silently broken
# (all previously passed against the pre-#20 frontmatter) -- these must
# clear the real relevance gate, though not necessarily with the same
# margin as the target queries above.
_NO_REGRESSION_CASES = [
    ("security-investment-prioritization", "how do I justify security spend to the CFO"),
    ("security-investment-prioritization", "respond to a budget challenge on my security budget"),
    ("security-investment-prioritization", "justify a strategic security hire"),
    (
        "security-investment-prioritization",
        "we need to purchase a security capability, how do I justify the cost",
    ),
    ("security-investment-prioritization", "build a business case for a new security control"),
    ("incident-escalation-framework", "when should I escalate this incident to the CISO"),
    (
        "incident-escalation-framework",
        "who needs to know about this security incident and how urgently",
    ),
]

# Real margin below the live gate's actual (possibly env-overridden)
# threshold -- not a hardcoded copy of its default, so this test tracks the
# real gate rather than drifting from it if KNOWLEDGE_DISTANCE_THRESHOLD
# ever changes. Deliberately modest: the two target queries measure at
# ~0.095 and ~0.012 below the default 0.55 threshold post-fix -- enough to
# catch "someone deleted the fix entirely" without being so strict a normal
# embedding-model version bump would make this test permanently red.
_TARGET_MARGIN = 0.01


@pytest.fixture(scope="module")
def indexed_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ChromaDBStore]:
    """Index every real BUILTIN skill (not just the two under test) so
    "ranks #1" is checked against real competitors, matching how #14 and
    #20 actually found and verified this gap. Deliberately excludes
    source=="company" skills -- a contributor's or CI runner's local
    company-skill fixtures under category=="security" could otherwise
    outrank the target and fail this test for reasons unrelated to these
    two builtin files."""
    tmp = tmp_path_factory.mktemp("skills_gap_regression")
    store = ChromaDBStore(persist_directory=str(tmp))
    for skill in list_skills():
        if skill.source == "builtin":
            index_skill(skill, store)
    yield store


def _top_hit(store: ChromaDBStore, query: str) -> dict:
    hits = search_skills(query, store, n_results=5, category_filter="security")
    assert hits, f"no hits at all for query {query!r}"
    return hits[0]


@pytest.mark.parametrize("target_name,query", _TARGET_CASES)
def test_target_query_ranks_first_with_margin(
    indexed_store: ChromaDBStore, target_name: str, query: str
) -> None:
    threshold = get_settings().knowledge_distance_threshold
    top = _top_hit(indexed_store, query)
    assert top["name"] == target_name, (
        f"{target_name!r} does not rank #1 for its own trigger query -- "
        f"{top['name']!r} (distance={top['distance']:.4f}) outranks it."
    )
    assert top["distance"] <= threshold - _TARGET_MARGIN, (
        f"{target_name!r} ranks #1 but at distance={top['distance']:.4f}, "
        f"not comfortably under the gate ({threshold} - margin {_TARGET_MARGIN}) "
        f"-- the frontmatter needs to name a term from the body the query "
        f"actually matches on. See .github/CONTRIBUTING.md -> 'Writing a Skill'."
    )


@pytest.mark.parametrize("target_name,query", _NO_REGRESSION_CASES)
def test_no_regression_query_still_clears_the_gate(
    indexed_store: ChromaDBStore, target_name: str, query: str
) -> None:
    threshold = get_settings().knowledge_distance_threshold
    top = _top_hit(indexed_store, query)
    assert top["name"] == target_name, (
        f"{target_name!r} no longer ranks #1 for {query!r} -- "
        f"{top['name']!r} (distance={top['distance']:.4f}) now outranks it."
    )
    assert top["distance"] <= threshold, (
        f"{target_name!r} used to clear the relevance gate for {query!r} "
        f"(distance={top['distance']:.4f} vs. threshold={threshold}) but no "
        f"longer does -- a frontmatter edit aimed at one query silently "
        f"broke this one. retrieve_skills() would now return nothing for it."
    )
