# OpenInfoSec

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Next.js 15](https://img.shields.io/badge/Next.js-15-black.svg)](https://nextjs.org/)

**A virtual security office for practicing the job of a CISO.**

OpenInfoSec simulates the Office of the CISO — a Chief Information Security Officer, a Director of Cyber Operations, and a Director of Governance, Risk & Compliance — alongside the cross-functional business stakeholders a real security leader has to work with every day: Finance, Legal, the CIO's office, the board. It's built for practicing the actual job: justifying a security budget to Finance, framing risk for a board that doesn't think in CVEs, negotiating an infrastructure tradeoff with IT, or reasoning through a live incident before the real stakes are real.

It is not a generic chatbot with a security-themed prompt. The security personas are grounded in published, cited practitioner research — including original work proposing a formal extension to the Purdue Enterprise Reference Architecture for cloud and AI governance in OT/ICS environments — not just what a language model already knows about NIST frameworks.

## Origin

OpenInfoSec is a fork of [SenteLabsAI/OpenExecutive](https://github.com/SenteLabsAI/OpenExecutive), an open-source virtual C-suite orchestrator that lets a person delegate to simulated business-executive specialists. OpenInfoSec keeps that orchestration engine and the original eight business department agents intact — a real CISO has to work *with* Finance, Legal, and the CIO's office, not in isolation from them — and adds a full security office on top.

**On independence:** this project started as a fork and currently still tracks its upstream for infrastructure fixes and orchestration improvements. The long-term intent is for OpenInfoSec to become its own thing — narrower in focus, opinionated about security practice, and eventually maintained as a standalone codebase rather than a permanent fork. Apache 2.0 attribution to the original project is preserved regardless of how far the two diverge.

## What It Does

Eleven specialist agents behind one coherent orchestrator voice:

**The Security Office** (OpenInfoSec's addition — pinned to a frontier model, grounded in original research):
- **CISO** — enterprise security strategy, risk posture, board-level security governance, IT/OT convergence
- **Director of Cyber Operations** — detection and monitoring, incident response, vulnerability management, OT/ICS operational security
- **Director of Governance, Risk & Compliance** — framework mapping (SOC 2, ISO 27001, NIST, HIPAA, NERC CIP, and more), audit preparation, policy management, regulatory tracking

**The Business Office** (inherited from OpenExecutive — the stakeholders a security leader actually has to work with):
- **Chief Strategy Officer** — competitive analysis, M&A, market positioning, OKRs
- **Chief Financial Officer** — financial modeling, fundraising, unit economics, cash flow
- **Chief HR/People Officer** — hiring, compensation, performance, culture
- **General Counsel** — contracts, IP, employment law basics, compliance
- **Chief Operating Officer** — process design, vendor management, operational scaling
- **Chief Marketing Officer** — GTM strategy, brand, communications, PR
- **Chief Product Officer** — roadmap, prioritization, product strategy
- **Board Communications Director** — board decks, investor relations, governance

All responses come from one consistent orchestrator voice. The internal agent architecture is never exposed to the user. The system maintains episodic memory of past decisions across sessions, and a built-in scheduler can proactively surface follow-ups and time-sensitive actions.

## Architecture

```
User message
    ↓
Executive Orchestrator (pinned model, independent of specialist models)
    ↓ tool use → forced consultation for on-topic security questions,
    │            free routing otherwise
    ↓ parallel specialist calls
CISO / Cyber Ops / GRC  (real frontier model — security is worth paying for)
CSO / CFO / CHRO / GC / COO / CMO / CPO / Board  (can run on a free local model)
    ↓ each specialist retrieves domain-filtered context from ChromaDB
Built-in knowledge (business MBA-level content + original security research)
    + your company documents
    ↓
Synthesized executive response
```

**Knowledge** — Two retrieval layers per specialist call: (1) built-in domain Markdown (`knowledge/builtin/`, git-tracked) seeded into ChromaDB at startup — for the security office this is grounded in original practitioner whitepapers and published analysis, not generic model knowledge — and (2) your uploaded company documents, chunked and stored in a separate `company_docs` collection. RAG context is injected into the user turn, never the cached system prompt.

**Model routing** — The orchestrator's own model (`EXECUTIVE_MODEL`) is configured independently of the department agents' default model (`DEFAULT_MODEL`). This matters: reliable tool-dispatch judgment — whether the orchestrator actually consults a specialist instead of answering solo — depends on running that judgment on a capable model, even if you're routing the department agents themselves to something cheaper or local.

**Episodic memory** — After every response, a background pass extracts key decisions and advice into SQLite. The next session opens with a `<past_decisions>` block.

**Departments** — Each specialist agent is wrapped in a persistent department record (charter, goals, cadences, and an `authority_level`: `auto_execute` / `propose_only` / `escalate`, defaulting to `propose_only` — nothing acts autonomously without a human in the loop unless explicitly configured otherwise). New departments require a one-time backfill against an already-seeded database; see `packages/core/openexecutive/departments/store.py`'s `backfill_missing_departments()`.

**Scheduler** — A built-in job runner claims due actions via `UPDATE … RETURNING` to prevent double-firing. The API must run as a single instance; do not horizontally scale it without gating the scheduler first.

**Prompt caching** — The system prompt is structured so the persona, company profile, and knowledge index are cached separately. No dynamic content ever goes in a cached block.

## Tech Stack

| Layer | Choice |
|---|---|
| LLM backbone | Anthropic Claude API (security office); optional local OpenAI-compatible server (business office) |
| Backend | Python 3.11+ + FastAPI |
| Package manager | `uv` |
| Vector store | ChromaDB (local, embedded) |
| Episodic memory | SQLite |
| Web UI | Next.js 15 (App Router) + Tailwind |
| License | Apache 2.0 |

## Quick Start

```bash
# Clone your fork
git clone https://github.com/SuperSchramm/OpenInfoSec.git
cd OpenInfoSec

# Set your Anthropic API key (required for the security office)
cp .env.example .env
# Edit .env: ANTHROPIC_API_KEY=sk-ant-...
# For the web UI's Google sign-in, also fill in the AUTH_* block — see docs/auth.md

# Start everything
make dev
```

Open http://localhost:3000. The API runs on port 8000, the UI on 3000.

> **First run:** requires Python 3.11+ and Node 22+. The initial `uv sync` pulls heavy ML dependencies, and the first boot downloads a small embedding model to build the local vector index — the first `make dev` takes a few minutes. Subsequent starts are fast.

**For contributors not using `make`:**

```bash
cd packages/core
uv sync
source .venv/bin/activate
uvicorn openexecutive.api.main:app --reload --port 8000

# In a second terminal
cd packages/ui && npm install && npm run dev
```

## Configuration

All settings via environment variables. Minimum required: `ANTHROPIC_API_KEY`, since the security office is deliberately pinned to Claude regardless of what else is configured.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key — powers the security office (CISO/CyberOps/GRC) and the orchestrator |
| `EXECUTIVE_MODEL` | No | falls back to `DEFAULT_MODEL` | The orchestrator's own model — set explicitly to keep tool-dispatch judgment reliable even if `DEFAULT_MODEL` points elsewhere |
| `DEFAULT_MODEL` | No | `claude-sonnet-4-6` | Model for the 8 business department agents (can point at a local model) |
| `FORCE_RESEED_KNOWLEDGE` | No | `false` | Bypasses the one-time knowledge-seeding no-op — set `true` for one `make dev` run after editing `knowledge/builtin/`, then unset it |
| `VECTOR_STORE_PATH` | No | `./chroma_db` | ChromaDB directory |
| `EPISODIC_DB_PATH` | No | `./episodic_memory.db` | SQLite for episodic memory, departments, alerts |
| `COMPANY_PROFILE_PATH` | No | `./company/profile.yaml` | Company profile |
| `ENABLE_CACHING` | No | `true` | Anthropic prompt caching |
| `LOCAL_MODELS_ENABLED` | No | `false` | Route the business department agents to a local OpenAI-compatible server (Ollama, LM Studio, vLLM) |
| `LOCAL_BASE_URL` | No | — | Local server URL incl. version path, e.g. `http://localhost:11434/v1` |
| `LOCAL_MODELS` | No | — | Comma-separated local model slugs |
| `AUTH_SECRET` / `AUTH_GOOGLE_ID` / `AUTH_GOOGLE_SECRET` / `AUTH_TRUST_HOST` | For web UI | — | Google OAuth for the web UI — see `docs/auth.md` |
| `ALLOWED_EMAILS` | For web UI | — | Comma-separated email allowlist for sign-in |

See [.env.example](.env.example) for the full list, including integration tokens (Slack, Discord, Telegram, Google Chat) inherited from upstream.

## Running the Security Office on a Real Model, Everything Else for Free

This is the intended cost model for local development and practice use, not just an option:

```bash
# .env — the security office always runs on Claude
ANTHROPIC_API_KEY=sk-ant-...
EXECUTIVE_MODEL=claude-sonnet-4-6

# The business departments run on a free local model instead
LOCAL_MODELS_ENABLED=true
LOCAL_BASE_URL=http://localhost:1234/v1   # LM Studio default; 11434 for Ollama
LOCAL_MODELS=your-local-model-slug
DEFAULT_MODEL=your-local-model-slug
```

`agents/ciso.py`, `agents/cyberops.py`, and `agents/grc.py` hardcode a Claude model string and deliberately ignore `DEFAULT_MODEL` — security judgment is this project's core value and is worth paying real API cost for. Measured cost for a full turn (orchestrator + two security specialists, Sonnet-tier) is roughly a penny. The business department agents carry no such pin and will happily run on whatever `DEFAULT_MODEL` points at.

**Caveats inherited from local-model support:** prompt caching and extended thinking have no local equivalent and are automatically disabled for local models. Multi-agent tool-use routing leans on the model's tool-calling reliability — smaller local models may route inconsistently, which is exactly why the orchestrator itself should stay on `EXECUTIVE_MODEL=claude-sonnet-4-6` rather than inheriting `DEFAULT_MODEL`.

## Adding a New Specialist Agent

1. Create `packages/core/openexecutive/agents/your_agent.py` extending `BaseAgent`
2. Add a system prompt constant in `packages/core/openexecutive/prompts/domain_prompts.py`
3. Register in `packages/core/openexecutive/orchestrator/router.py` — add to `SPECIALIST_REGISTRY`, `SPECIALIST_DESCRIPTIONS`, and (if the agent should be reliably consulted rather than left to model discretion) `SPECIALIST_KEYWORDS`
4. Add domain alias to `DOMAIN_ALIASES` in `packages/core/openexecutive/knowledge/retriever.py`, and to `DOMAIN_MAP` in `knowledge/loader.py`
5. Add a department entry to `DEFAULT_DEPARTMENTS` in `departments/charters/__init__.py` — note this only seeds on a fresh database; an existing one needs `backfill_missing_departments()`
6. Add knowledge docs to `knowledge/builtin/your_domain/`, then run once with `FORCE_RESEED_KNOWLEDGE=true`
7. Add at least 2 eval scenarios to `evals/scenarios/`

## Development

```bash
make dev          # Start FastAPI + Next.js
make test         # Run Python tests
make eval         # Run eval suite
make lint         # Run ruff + mypy
make docker       # Build and run Docker stack
```

## Evaluation System

`evals/scenarios/` contains scenarios across all department domains, including dedicated CISO/Cyber Ops/GRC scenarios grounded in the security office's actual seeded knowledge (e.g., verifying a GRC response correctly reflects specific regulatory reporting windows). Scored by an LLM-as-judge across five dimensions: persona coherence, domain accuracy, company-context utilization, routing quality, and actionability.

## Onboarding Your Company

The first time you visit the app, you'll be guided through a wizard to set up your company profile — industry, business model, competitive landscape, strategic priorities, culture. After onboarding, every specialist references your specific company context.

## Interfaces

| Interface | How to Use |
|-----------|-----------|
| **Web UI** | `http://localhost:3000` |
| **Slack / Email / Telegram / Google Chat / Discord** | Inherited from upstream — see `docs/` for setup per integration |
| **CLI** | `openexecutive chat` |

## Document Upload

```bash
# Via CLI
openexecutive upload deck.pdf model.xlsx strategy.md

# Via API
curl -X POST http://localhost:8000/documents \
  -F "file=@deck.pdf" \
  -F "domain=strategy"
```

## Privacy

Everything in `company/` is gitignored — the profile YAML, uploaded documents, and the ChromaDB vector store. `docs/source-material/` (original whitepapers used to ground the security office's knowledge) is also gitignored — none of this leaves your local machine except as part of prompts sent to the Anthropic API. Anthropic does not train on API data.

## Deployment

Inherited Fly.io deployment tooling exists (`fly.api.toml`, `fly.ui.toml`, `docs/deployment.md`) but has not been used for OpenInfoSec — development so far has been entirely local. If you deploy this fork, review `docs/deployment.md`'s single-instance scheduler constraint and the auth/shared-secret setup in `docs/auth.md` before doing so.

## Credits

Built on [OpenExecutive](https://github.com/SenteLabsAI/OpenExecutive) by SenteLabsAI, licensed under Apache 2.0. See `LICENSE` and `NOTICE`.

Security domain research and personas by Kevin Schramm, CISM — cybersecurity specialist in energy (LNG/clean power) and healthcare OT, author of *Hacking Your Career Path: The IT to Cybersecurity Transition*.

## License

Apache 2.0 — free to use commercially, requires attribution. See `LICENSE`.
