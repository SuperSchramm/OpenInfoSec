from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any

from openexecutive.agents.base import BaseAgent

if TYPE_CHECKING:
    from openexecutive.orchestrator.debug_events import DebugCollector
from openexecutive.agents.board_comms import BoardCommsAgent
from openexecutive.agents.ciso import CISOAgent
from openexecutive.agents.cyberops import CyberOpsAgent
from openexecutive.agents.finance import FinanceAgent
from openexecutive.agents.grc import GRCAgent
from openexecutive.agents.hr_talent import HRAgent
from openexecutive.agents.legal import LegalAgent
from openexecutive.agents.marketing import MarketingAgent
from openexecutive.agents.operations import OperationsAgent
from openexecutive.agents.product import ProductAgent
from openexecutive.agents.strategy import StrategyAgent
from openexecutive.agents.talent import TalentAgent
from openexecutive.agents.triage import TriageAgent

SPECIALIST_REGISTRY: dict[str, BaseAgent] = {
    "cso": StrategyAgent(),
    "cfo": FinanceAgent(),
    "chro": HRAgent(),
    "gc": LegalAgent(),
    "coo": OperationsAgent(),
    "cmo": MarketingAgent(),
    "cpo": ProductAgent(),
    "ciso": CISOAgent(),
    "cyberops": CyberOpsAgent(),
    "grc": GRCAgent(),
    "board_comms": BoardCommsAgent(),
    "talent": TalentAgent(),
    "triage": TriageAgent(),
}

SPECIALIST_DESCRIPTIONS = {
    "cso": "Chief Strategy Officer — competitive analysis, M&A, market positioning, scenario planning, OKRs",
    "cfo": "Chief Financial Officer — financial modeling, unit economics, fundraising, cash flow, board finance",
    "chro": "Chief HR/People Officer — hiring, compensation, performance management, culture, org design",
    "gc": "General Counsel — contracts, IP, employment law basics, compliance (with appropriate disclaimers)",
    "coo": "Chief Operating Officer — process design, vendor management, operational scaling, metrics",
    "cmo": "Chief Marketing Officer — GTM strategy, brand, messaging, PR, crisis communications",
    "cpo": "Chief Product Officer — product roadmap, prioritization frameworks, product strategy",
    "board_comms": "Board Communications Director — board decks, investor relations, governance",
    "ciso": "Chief Information Security Officer — security strategy, risk posture, board reporting, cross-domain security governance",
    "cyberops": "Director of Cyber Operations — SOC/IR, threat detection, OT/ICS security, vulnerability management, incident response",
    "grc": "Director of Governance, Risk & Compliance — framework mapping, audit prep, policy, regulatory compliance",
    "talent": "Head of Talent & Executive Search — candidate screening & fit scoring, executive sourcing, energy-sector talent-market mapping",
    "triage": "Chief of Staff — evaluates inbound events (email/Slack/docs) for significance and decides alerting",
}

# Specialists a chat consult (the `consult_specialist` tool, in whichever
# surface calls route_to_specialist) may address. Excludes "triage" -- it is
# meta-routing (inbound-event significance triage for the alert pipeline),
# not a domain specialist, and its real call path never goes through
# route_to_specialist: alerts/pipeline.py instantiates TriageAgent() directly
# and calls its own .triage() method. Mirrors the same exclusion
# orchestrator/committee.py already applies when picking domain reviewers
# ("triage" is meta-routing, not a domain). Single source of truth so the
# tool schema's enum and route_to_specialist's dispatch check can't drift
# from each other the way SPECIALIST_REGISTRY-membership checks did.
CHAT_CONSULTABLE_SPECIALISTS = frozenset(SPECIALIST_REGISTRY) - {"triage"}


# Cheap keyword heuristic — does a user message plausibly touch a specialist
# domain? NO LONGER gates the forced tool_choice in executive._stream_agent_loop
# (see is_off_topic() below for that) -- it is a flat OR across every
# specialist's combined list ("does this match anyone's domain," not "does
# this match the right domain"), and gating forcing on it missed real
# domain-specific queries whose wording didn't happen to hit a keyword: an
# OT/shadow-AI incident query matched zero keywords across all 13
# specialists and silently fell back to unforced "auto" on exactly the
# domain forcing exists to protect. Kept as an observability signal only --
# logged per turn in executive.py so keyword-list coverage can still be
# reviewed/tuned without being load-bearing for correctness. Broadening a
# specialist's list here improves that signal's sensitivity; it does not
# change which specialist the model picks once a consult is forced --
# that's the model's own judgment inside the tool call, unaffected by this
# dict. `triage` is intentionally excluded — it fields inbound events, not
# something a user asks about directly.
SPECIALIST_KEYWORDS: dict[str, list[str]] = {
    "cso": ["competitive strategy", "competitor", "market entry", "acquisition",
            "merger", "m&a", "positioning", "scenario planning", "okr",
            "market sizing", "beachhead", "moat"],
    "cfo": ["budget", "financial model", "cash flow", "runway", "burn rate",
            "unit economics", "ltv", "cac", "fundraising", "valuation",
            "cap table", "term sheet", "gross margin", "arr", "investor"],
    "chro": ["hiring", "compensation", "salary", "equity grant",
             "performance review", "pip", "org design", "onboarding",
             "termination", "layoff", "headcount"],
    "gc": ["contract", "nda", "ip ownership", "trademark", "patent",
           "lawsuit", "gdpr", "ccpa", "non-compete", "employment law",
           "indemnif"],
    "coo": ["vendor", "sla", "operational process", "supply chain",
            "logistics", "bottleneck", "procurement"],
    "cmo": ["go-to-market", "gtm", "brand positioning", "messaging",
            "pr strategy", "crisis communication", "campaign",
            "demand generation", "nps"],
    "cpo": ["product roadmap", "product strategy", "prioritization",
            "rice score", "product-market fit", "pmf", "customer discovery",
            "build vs buy"],
    "board_comms": ["board deck", "board meeting", "investor update",
                     "investor relations", "board member", "governance structure",
                     "shareholder"],
    "talent": ["candidate", "executive search", "fit score",
               "screen candidate", "sourcing", "recruit", "talent pipeline",
               "search mandate"],
    "ciso": ["security strategy", "risk register", "risk appetite",
             "security posture", "security architecture", "security roadmap",
             "ai governance", "security investment", "security overhaul"],
    "cyberops": ["incident response", "siem", "soc alert", "vulnerability",
                 "patch", "ransomware", "malware", "phishing", "breach",
                 "ics security", "ot security"],
    "grc": ["compliance framework", "soc 2", "iso 27001", "audit prep",
            "nist", "hipaa", "regulatory obligation", "policy review"],
}


def plausibly_on_topic(user_message: str) -> bool:
    """True if ``user_message`` plausibly touches a specialist domain.

    Simple case-insensitive substring match against SPECIALIST_KEYWORDS.
    Not a classifier — a cheap gate for whether to nudge tool use, not a
    routing decision (the model still picks which specialist(s) to call).
    """
    lowered = user_message.lower()
    return any(
        keyword in lowered
        for keywords in SPECIALIST_KEYWORDS.values()
        for keyword in keywords
    )


# Narrow allowlist of message shapes where executive._stream_agent_loop
# deliberately withholds the forced consult_specialist tool_choice on
# iteration 1. Everything else forces by default -- the inverse of the old
# plausibly_on_topic()-gated design (see the SPECIALIST_KEYWORDS comment
# above for why that design was replaced). This is a security-adjacent
# advisory tool: the safe failure direction is over-forcing (a consult that
# wasn't strictly necessary), not under-forcing (a solo answer on a question
# that needed one), so keep this allowlist narrow and prefer false negatives
# (falls through to forced) over false positives (wrongly skips forcing).
#
# Exact-match on the WHOLE normalized message, deliberately not a
# prefix/substring regex on either category:
#   - a greedy prefix like `^(hi|hey)\b.*` would misclassify "Hey, can you
#     review our NDA terms?" as small talk just because of its opener;
#   - a substring search for meta-question phrasing (an earlier version of
#     this function used one) misclassified genuine on-topic questions --
#     "What can you do about the ransomware on our OT segment?" and "Who
#     are you recommending we hire as our first CISO?" both contain a
#     matched substring while being squarely on-topic. Worse, `user_message`
#     here is the caller's already attachment-augmented text
#     (api/routes/chat.py appends extracted document text after the user's
#     own words), so a substring search is scanning content the user didn't
#     even write -- a boilerplate FAQ line inside an uploaded document could
#     silently withhold forcing on a real security question.
# Exact-match-on-the-whole-message closes both: any additional real content,
# whether the user's own elaboration or appended attachment text, breaks the
# match and correctly falls through to forced.
#
# Normalization (`_normalize`): lowercase, strip everything but letters/
# digits/whitespace (all punctuation, including apostrophes, so "how's it
# going" and "hows it going" normalize identically), collapse whitespace.
# To extend either category below, add the phrase already in that
# normalized form (lowercase, no punctuation, single spaces).
_OFF_TOPIC_PHRASES: frozenset[str] = frozenset({
    # Greetings / small talk
    "hi", "hello", "hey", "hi there", "hello there", "hey there",
    "good morning", "good afternoon", "good evening",
    "hi how are you", "hello how are you", "hey how are you",
    "how are you", "hows it going", "how are you doing",
    "thanks", "thank you", "thanks that helps", "thank you that helps",
    "sounds good", "got it", "ok", "okay", "great", "perfect", "cool",
    "bye", "goodbye", "see you", "talk soon",
    # Meta-questions about the tool/orchestrator itself
    "how do you work", "how does this work", "how does this tool work",
    "how does this system work", "how does this app work",
    "how does this assistant work", "what model are you",
    "what model do you use", "are you an ai", "are you a bot",
    "are you a chatbot", "what can you do", "who are you",
    "who made you", "who built you",
    # Short affirmations / negations / conversational filler. Exact-match
    # keeps these safe to add: "yes" only matches the literal word "yes",
    # never "yes, and also patch the CVE" (that normalizes to a longer
    # string with no set membership, so it still forces). Added after an
    # adversarial review round found these fell through to forced by
    # default -- correct in direction but a real per-turn cost multiplier on
    # a chat product's highest-frequency turn shapes.
    "yes", "no", "sure", "maybe", "yes please", "no thanks",
    "ok thanks", "okay thanks", "great thanks", "sounds great",
    "never mind", "nevermind", "makes sense", "understood", "noted",
    "will do", "roger that", "one moment", "hold on", "continue",
    "go on", "please continue", "can you repeat that",
})

# Strip everything except letters/digits/whitespace (all punctuation,
# including apostrophes, so "how's it going" and "hows it going" normalize
# identically), then collapse whitespace to single spaces. A message that
# normalizes to "" (e.g. a bare emoji reaction, or pure punctuation) is
# handled by is_off_topic() directly, not by this function. To extend
# _OFF_TOPIC_PHRASES, add the new phrase already in this normalized form:
# lowercase, no punctuation, single spaces.
_NORMALIZE_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(user_message: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace -- see the
    comment above _NORMALIZE_RE for the exact rules and rationale."""
    stripped = _NORMALIZE_RE.sub("", user_message.strip().lower())
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def is_off_topic(user_message: str) -> bool:
    """True only for the narrow allowlist where withholding the forced
    consult_specialist tool_choice is deliberately safe: greetings/small
    talk, a meta-question about the tool/orchestrator itself, or a short
    affirmation/filler reply -- and only when the ENTIRE message (after
    normalization, see _normalize()) is one of the curated phrases, or
    normalizes to nothing at all (e.g. a bare emoji reaction). Everything
    else returns False, i.e. forces by default -- see the comment block
    above _OFF_TOPIC_PHRASES for why exact-match-on-the-whole-message is the
    safe design here.
    """
    if not user_message.strip():
        return True
    normalized = _normalize(user_message)
    if not normalized:
        return True
    return normalized in _OFF_TOPIC_PHRASES


SPECIALIST_TOOLS: list[dict[str, Any]] = [
    {
        "name": "consult_specialist",
        "description": (
            "Consult a specialist executive agent for domain-specific analysis. "
            "Use this to get deep expertise from the relevant functional leader. "
            "You may call this multiple times in parallel for cross-domain questions. "
            "Call this proactively whenever the user's question substantively touches "
            "one of the specialist domains below — do not rely on your own general "
            "knowledge alone for domain-specific guidance; specialists have "
            "company-specific context you do not have directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "specialist": {
                    "type": "string",
                    "enum": sorted(CHAT_CONSULTABLE_SPECIALISTS),
                    "description": (
                        "Which specialist to consult. Options: "
                        + ", ".join(
                            f"{k} ({v})"
                            for k, v in SPECIALIST_DESCRIPTIONS.items()
                            if k in CHAT_CONSULTABLE_SPECIALISTS
                        )
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "The specific question or task for the specialist. Be precise — they only see this query and the conversation context.",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant context from the conversation that the specialist needs to give a good answer.",
                },
            },
            "required": ["specialist", "query"],
        },
    }
]


async def route_to_specialist(
    specialist_name: str,
    query: str,
    context: str = "",
    retrieved_knowledge: str = "",
    episodic_context: str = "",
    failure_cases: str = "",
    department_memory: str = "",
) -> str:
    if specialist_name not in CHAT_CONSULTABLE_SPECIALISTS:
        if specialist_name in SPECIALIST_REGISTRY:
            # A real registry member (e.g. "triage") that isn't a domain
            # specialist -- reject with a specific, actionable message
            # rather than the generic "Unknown specialist" used below, and
            # fail closed by returning a string (not raising): this runs
            # inside route_parallel's asyncio.gather(), so raising here
            # would cancel every sibling specialist call dispatched in the
            # same turn instead of just failing this one tool_use.
            return (
                f"{specialist_name!r} is not available via consult_specialist "
                "(internal/meta-routing agent, not a domain specialist). "
                f"Choose one of: {', '.join(sorted(CHAT_CONSULTABLE_SPECIALISTS))}."
            )
        return f"Unknown specialist: {specialist_name}"
    agent = SPECIALIST_REGISTRY.get(specialist_name)
    if agent is None:
        # Defensive only -- CHAT_CONSULTABLE_SPECIALISTS is a subset of
        # SPECIALIST_REGISTRY by construction, so this shouldn't be
        # reachable today. Kept as a graceful fallback (not a bare index)
        # so a future divergence between the two degrades to the same
        # fail-closed string response instead of a KeyError propagating out
        # of route_parallel's asyncio.gather() and cancelling every sibling
        # specialist call dispatched in the same turn.
        return f"Unknown specialist: {specialist_name}"
    return await agent.analyze(
        query=query,
        context=context,
        retrieved_knowledge=retrieved_knowledge,
        episodic_context=episodic_context,
        failure_cases=failure_cases,
        department_memory=department_memory,
    )


# Tool_result returned for consult_specialist calls past the per-turn fan-out
# cap, so the model sees an explicit acknowledgement and can re-ask next turn
# rather than the extra calls being silently dropped.
FANOUT_SKIP_MESSAGE = (
    "Skipped: this turn already dispatched the maximum number of parallel "
    "specialist consultations (cap={cap}). Ask again in a follow-up turn if "
    "this specialist's input is still needed."
)


def resolve_fanout_cap(max_parallel: int) -> int:
    """Effective per-turn specialist fan-out cap.

    ``max_parallel <= 0`` falls back to the specialist roster size, so the
    default (0) is inert — no real cross-domain turn consults more distinct
    specialists than exist. A positive value bounds pathological runaway. The
    floor of 1 keeps the cap from ever zeroing out dispatch (belt-and-suspenders
    against an empty roster).
    """
    return max_parallel if max_parallel > 0 else max(len(SPECIALIST_REGISTRY), 1)


def partition_specialist_fanout(
    tool_uses: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    max_parallel: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], int]:
    """Split a turn's specialist tool_uses/calls at the fan-out cap.

    ``tool_uses`` and ``calls`` must be 1:1 in the same order. Returns
    ``(run_tool_uses, run_calls, skipped_results, cap)``:

      - ``run_tool_uses`` / ``run_calls`` — the first ``cap`` to dispatch,
        aligned and equal length.
      - ``skipped_results`` — maps each over-cap tool_use id to the formatted
        skip message, so the caller can hand EVERY consult_specialist tool_use
        a tool_result (Anthropic requires one result per tool_use).
      - ``cap`` — the resolved cap, for instrumentation.
    """
    cap = resolve_fanout_cap(max_parallel)
    run_tool_uses = tool_uses[:cap]
    run_calls = calls[:cap]
    skipped_results = {
        tu["id"]: FANOUT_SKIP_MESSAGE.format(cap=cap) for tu in tool_uses[cap:]
    }
    return run_tool_uses, run_calls, skipped_results, cap


async def _retrieve_for_call(call: dict[str, str]) -> str:
    """Run a per-specialist, domain-filtered vector retrieval for one tool call."""
    from openexecutive.knowledge.retriever import retrieve

    return await asyncio.to_thread(
        retrieve, query=call["query"], specialist_name=call["specialist"]
    )


async def _retrieve_failures_for_call(call: dict[str, str]) -> str:
    """Domain-filtered failure case retrieval for one specialist call."""
    from openexecutive.knowledge.retriever import retrieve_failures

    return await asyncio.to_thread(
        retrieve_failures, query=call["query"], specialist_name=call["specialist"]
    )


async def _prefetch_department_for_call(
    call: dict[str, str], session_id: str | None
) -> str:
    """Department-memory prefetch for one specialist call.

    Resolves ``specialist → department_slug`` via the departments
    registry and queries the dept peer's Honcho representation. Returns
    "" when the specialist has no owning department (e.g. ``triage``)
    or when Honcho is disabled / fails — the wrapper's own degrade-on-
    failure semantics already audit the outcome.
    """
    from openexecutive.departments.registry import slug_for_specialist
    from openexecutive.memory.honcho_client import prefetch_department

    slug = slug_for_specialist(call["specialist"])
    if slug is None:
        return ""
    return await prefetch_department(
        query=call["query"],
        department_slug=slug,
        session_id=session_id,
    )


async def route_parallel(
    calls: list[dict[str, str]],
    retrieved_knowledge_map: dict[str, str] | None = None,
    episodic_context: str = "",
    session_id: str | None = None,
    debug_collector: DebugCollector | None = None,
) -> list[str]:
    """Execute multiple specialist calls concurrently.

    Each specialist receives its own domain-filtered RAG context, fetched
    in parallel before the LLM calls fire. Callers may still supply a
    pre-built ``retrieved_knowledge_map`` (keyed by specialist name) to
    short-circuit the per-call retrieval — useful for tests or when the
    caller has already gathered shared context.

    ``episodic_context`` is per-turn (not per-specialist) and forwarded to
    every specialist in this batch.

    ``session_id`` (when provided) is threaded into the per-specialist
    department-memory prefetch for audit grouping. Specialists whose
    department has institutional Honcho memory receive a
    ``<department_memory>`` block synthesized from that dept peer's
    representation; specialists without an owning department (e.g.
    ``triage``) skip the prefetch entirely.

    Returns results in the same order as ``calls`` so callers can zip
    with tool_use_ids.
    """
    if retrieved_knowledge_map is None:
        knowledge_futures = [_retrieve_for_call(c) for c in calls]
        failures_futures = [_retrieve_failures_for_call(c) for c in calls]
        all_results = await asyncio.gather(*knowledge_futures, *failures_futures)
        mid = len(calls)
        knowledge_per_call = list(all_results[:mid])
        failures_per_call = list(all_results[mid:])
    else:
        knowledge_per_call = [
            retrieved_knowledge_map.get(c["specialist"], "") for c in calls
        ]
        failures_per_call = [""] * len(calls)

    # Fan out dept-memory prefetch alongside knowledge/failures. Each call
    # is cheap when Honcho is disabled or when the specialist has no
    # owning dept (returns "" immediately), so unconditionally gathering
    # keeps the per-call critical path uniform.
    dept_memory_per_call = list(
        await asyncio.gather(
            *(_prefetch_department_for_call(c, session_id) for c in calls)
        )
    )

    async def call_one(idx: int, call: dict[str, str]) -> str:
        specialist = call["specialist"]
        if debug_collector:
            debug_collector.emit("specialist_start", {
                "specialist": specialist,
                "query": call["query"],
                "retrieved_chars": len(knowledge_per_call[idx]),
                "failures_chars": len(failures_per_call[idx]),
                "department_memory_chars": len(dept_memory_per_call[idx]),
            })
        t_start = time.monotonic()
        result = await route_to_specialist(
            specialist_name=specialist,
            query=call["query"],
            context=call.get("context", ""),
            retrieved_knowledge=knowledge_per_call[idx],
            episodic_context=episodic_context,
            failure_cases=failures_per_call[idx],
            department_memory=dept_memory_per_call[idx],
        )
        if debug_collector:
            debug_collector.emit("specialist_done", {
                "specialist": specialist,
                "duration_ms": round((time.monotonic() - t_start) * 1000),
                "response_preview": result[:120],
                "response_length": len(result),
            })
        return result

    return list(await asyncio.gather(*(call_one(i, c) for i, c in enumerate(calls))))
