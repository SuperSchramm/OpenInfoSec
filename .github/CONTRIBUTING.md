# Contributing to Open Executive

## Getting Started

1. Fork the repo and clone your fork
2. Set up the development environment: `make install`
3. Copy `.env.example` to `.env` and add your `ANTHROPIC_API_KEY`
4. Start the dev server: `make dev`
5. Run the tests: `make test`

## Branch Naming

- `feat/` — new features
- `fix/` — bug fixes
- `agent/` — new specialist agents
- `eval/` — new eval scenarios
- `docs/` — documentation changes

## PR Requirements

All PRs must:
1. Pass CI (ruff, mypy, unit tests)
2. Include working code — no stubs, no placeholders
3. Include tests for new behavior
4. For new or modified agents: include at least 2 eval scenarios
5. **Architecture docs**: verify `/architecture` reflects your change (see below)
6. **A completed PR description using the template** — what changed, why, and
   how it works, with the checklist filled in. PRs submitted with an empty
   template will be closed; you're welcome to resubmit with the sections
   completed.

## Adding a New Specialist Agent

See [CLAUDE.md](../CLAUDE.md#adding-a-new-specialist-agent) for the step-by-step guide.

## Improving the Knowledge Base

The `knowledge/` directory contains Markdown files with executive expertise. Contributions here are very welcome.

Requirements:
- Accurate and up-to-date information
- Cite sources for specific claims
- Domain-tagged with the correct folder
- Practical, not academic — this is for practitioners

## Writing a Skill

Skills (`knowledge/builtin/skills/<category>/*.md`) are a different authoring
surface from plain knowledge docs above: they're found by semantic search
over their YAML frontmatter, not their body. `description` and `when_to_use`
are the **entire search corpus** — `skills_index._skill_doc_text()` embeds
`name + description + when_to_use` and nothing else. A body section covering
a specific technique or scenario that isn't named in the frontmatter is
invisible to search, no matter how well-written the body is.

This bit contributors before (issue #14, #20 — 7 of 10 files in one
directory shipped with this exact gap). A compliant `description`/
`when_to_use` pair:

1. **Names the specific triggering scenario(s)**, not just the abstract
   purpose — "GRC says compliant, CyberOps says it doesn't work — whose
   call?" beats "determine whether a question is its own to answer."
2. **Names any specific technique, formula, taxonomy, or rubric the body
   uses**, verbatim or close to it — `SLE/ARO/ALE`, a named framework
   (`NIST CSF`, `MITRE ATT&CK`), a specific taxonomy the body defines — not
   a paraphrase like "quantify risk" or "assess an incident."
3. **Gets tested against the real embedding index before merging, with
   MORE than one query.** Read-through review alone isn't enough — it both
   misses real gaps (a well-written paragraph can still fail to clear the
   relevance threshold on the exact query it's meant to answer) and
   produces false positives (a file that reads like it needs work may
   already clear threshold with room to spare). And testing only the one
   query you're trying to fix isn't enough either — a rewrite chasing one
   query's distance number down can silently push OTHER, previously-passing
   queries over the threshold (this happened during #20's own fix: fixing
   the target query broke five queries that used to work). Test a handful
   of realistic phrasings, not just the one that prompted the change.

To test, run real queries against the real index (adjust `queries` and
`category_filter` to your skill, run from `packages/core`):

```python
import tempfile
from openexecutive.knowledge.skills_index import index_skill, search_skills
from openexecutive.knowledge.skills_repo import list_skills
from openexecutive.knowledge.store import ChromaDBStore

with tempfile.TemporaryDirectory() as tmp:
    store = ChromaDBStore(persist_directory=tmp)
    for skill in list_skills():
        if skill.source == "builtin":  # matches real competition, skip local company fixtures
            index_skill(skill, store)

    queries = [
        "a real question a user would actually ask",
        "a second, differently-phrased question the skill should also catch",
    ]
    for query in queries:
        hits = search_skills(query, store, n_results=5, category_filter="security")
        print(f"\n{query!r}")
        for h in hits:
            print(f"  {h['name']:40s} distance={h['distance']:.4f}")
```

(Needs `ANTHROPIC_API_KEY` and `EXEC_EMAIL_ADDRESS` set to any value —
`Settings()` requires them but this script never calls the API. Nothing
here is async — `index_skill`/`search_skills`/`list_skills` are all
synchronous, no event loop needed.)

Your skill should rank #1 for each of its trigger queries, with a
comfortable margin under `settings.knowledge_distance_threshold`
(`openexecutive/config.py`, default `0.55`, overridable via
`KNOWLEDGE_DISTANCE_THRESHOLD` — this is the threshold `retrieve_skills()`
actually gates on; don't confuse it with the separate `_DISTANCE_THRESHOLD`
constant in `retriever.py`, which governs the unrelated `retrieve()` path)
— not just barely clearing it. If a query doesn't clear it, or clearing it
pushed another query over, the frontmatter needs a term from the body that
query actually matches on, not a longer paraphrase of what's already
there.

## Architecture Docs (`/architecture` page)

The `/architecture` page in the UI is served from **static, hand-authored
content**: one `packages/core/openexecutive/architecture/prebuilt/<section_id>.json`
file per section listed in `architecture/sections.py`. Nothing on that path
calls an LLM at runtime, so nothing updates itself — if your PR changes
behavior a section describes and you don't re-author the section, the page
silently goes stale.

The deep source-of-truth notes behind the page live in
`packages/core/openexecutive/architecture/architecture-facts.yaml`.

**When your PR materially changes a documented topic, update BOTH in the same
PR**: the relevant `architecture-facts.yaml` key, and the affected
`prebuilt/<section_id>.json`. This applies equally to *changes* under an
existing topic (e.g. adding a new integration channel, changing a documented
endpoint's response shape) — not just brand-new topics. The topic → section-id
map and full procedure are in [CLAUDE.md](../CLAUDE.md#architecture-docs);
common cases:

- New or changed integration channel → `integrations`
- New workflow primitive or routing pattern → `workflows` / `agents` / `lifecycle`
- Cache layout change → `caching`
- Endpoint added/removed/renamed or response shape changed → `api`
- New top-level module under `packages/core/openexecutive/` → new `SectionSpec`
  in `architecture/sections.py`, matching entry in
  `packages/ui/src/app/architecture/page.tsx`, and a new `prebuilt/<id>.json`

Each `prebuilt/<id>.json` carries `section_id`, `title`, `markdown`, `mermaid`
(string or `null`), and `generated_at`; validate edits with
`python -m json.tool`. Pure additions to `SPECIALIST_REGISTRY` are
auto-reflected in the `agents` facts and need no YAML edit.

`architecture-facts.yaml` is almost all hand-authored prose, and an unquoted
scalar value containing `: ` (colon-space) reads as a nested mapping key to
YAML and fails to parse (issue #6) — quote such values or put them in a block
literal (`key: |`). `make lint` and CI now run
`python scripts/validate_facts_yaml.py` to catch this before it reaches the
full test suite; run it directly after editing the file if you want faster
feedback: `cd packages/core && uv run python scripts/validate_facts_yaml.py`.

## Prompt Changes

Prompt changes to `executive_persona.py` or `domain_prompts.py` require:
1. A before/after comparison in the PR description
2. Eval suite run showing no regression (score drop ≤10% on existing scenarios)
3. At least 2 new eval scenarios if adding new behavior

## Reporting Issues

Use GitHub Issues. Include:
- What you asked the Executive
- What you expected
- What you got
- Your company profile context (anonymized)
