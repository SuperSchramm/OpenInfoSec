# OpenInfoSec — Architecture

> **This file is intentionally short.** Earlier versions of this doc
> duplicated specific counts and tables (specialist lists, domain aliases,
> workflow lists, config variables, API endpoints) that went stale
> repeatedly as the system grew — see issue #24. Those specifics now live
> in exactly one place each, listed below, so there's nothing here to keep
> back in sync.

## What this is

OpenInfoSec is a multi-agent AI system built on the Open Executive
architecture: the user always interacts with one coherent voice — the
Executive — which internally routes to domain specialist agents (security,
GRC, finance, legal, HR, and others), retrieves relevant knowledge via RAG,
and synthesizes everything into a single response. The internal agent
architecture is never exposed to the user.

Built on the Anthropic Claude API with native tool use — no LangGraph, no
CrewAI, just Python, FastAPI, and direct Anthropic SDK calls.

## Repository layout

```
packages/core/          Python backend (FastAPI + the agent system)
packages/ui/             Next.js 15 web UI
knowledge/                Curated knowledge base (git-tracked Markdown)
evals/                     Eval scenarios + LLM-as-judge runner
docker/                    Dockerfile + docker-compose.yml
docs/                      This file + setup/deployment guides
```

`packages/core/openexecutive/` is organized by concern (orchestrator,
agents, knowledge, memory, integrations, workflows, scheduler, alerts,
audit, and more) — browse the directory itself for the current module
list rather than trusting a tree that will drift here.

## Where to find current, authoritative specifics

| Topic | Source of truth |
|---|---|
| What specialists exist, their models and domains | `orchestrator/router.py` → `SPECIALIST_REGISTRY`, and each `agents/*.py` |
| How knowledge retrieval is domain-scoped | `knowledge/retriever.py` → `DOMAIN_ALIASES` |
| Available workflows | `workflows/__init__.py` → `WORKFLOW_REGISTRY` |
| Integrations (Slack, Discord, Telegram, Google Chat, Email) | `integrations/` — one module per channel |
| API endpoints | `api/routes/` — one router module per resource, or the live OpenAPI docs at `/docs` on a running instance |
| Configuration / environment variables | `.env.example` at the repo root |
| Repo conventions, commands, PR requirements | [`CLAUDE.md`](../CLAUDE.md) |
| Deep architectural narrative (integrations, caching, routing, invariants) | `packages/core/openexecutive/architecture/architecture-facts.yaml` — the curated notes behind the live `/architecture` page |
| The rendered, always-current architecture walkthrough | the `/architecture` page in a running instance of the UI |

## Company data privacy

Everything under `packages/core/company/` is gitignored:

- `company/profile.yaml` — structured company profile
- `company/docs/` — uploaded documents
- the vector store — embeddings of company documents
- the episodic-memory database — decisions, initiatives, advice, alerts, audit log

None of this leaves the local machine (or your own deployment volume) except
as part of prompts sent to the Anthropic API. Anthropic does not train on
API data.
