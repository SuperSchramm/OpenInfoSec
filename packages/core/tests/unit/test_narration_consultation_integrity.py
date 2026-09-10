"""Audit probe: does anything catch the Executive claiming a specialist was
consulted when no consult ever happened?

executive_persona.py's rules ("Never reference your internal architecture...
no mention of specialists" / "Do not narrate your reasoning process... do
not say 'let me check'") are prompt-only instructions. `_stream_agent_loop`
now inspects the model's own output text against what was actually
dispatched via `consult_specialist` (see `orchestrator.narration_integrity`)
and logs a `narration_policy_violation` audit row when they disagree --
log-only, it never blocks/retries/modifies the response. This file scripts
a response where the model's final text explicitly claims a consult
occurred but the scripted turn returns zero tool_use blocks (no real
consult_specialist call, so route_parallel/debug_collector never fire
specialist_start/specialist_done), and asserts that a fabricated claim is
either backed by a real specialist_start/specialist_done pair, or -- since
this check is log-only and never prevents the fabrication itself -- by a
narration_policy_violation audit row recording that it was caught.

`test_fabricated_consultation_language_has_no_backing_log_entry` used to
FAIL against pre-guard code (nothing stopped the fabricated response from
reaching the user, and nothing logged it either). It now passes: the
response still ships unmodified, but the runtime guard records the
violation. The other two tests are controls proving the check itself
doesn't false-positive on an honest response, and doesn't require a
violation row when the claim is genuinely backed by a real consult.

Fake provider plumbing mirrors test_specialist_tool_choice.py's pattern.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openexecutive.audit import AuditLogger, set_audit_logger
from openexecutive.orchestrator.debug_events import DebugCollector
from openexecutive.orchestrator.executive import Executive
from openexecutive.orchestrator.narration_integrity import (
    _ALIAS_TO_SPECIALIST_KEY,
    find_consultation_claim,
    narration_policy_violation,
)
from openexecutive.orchestrator.router import CHAT_CONSULTABLE_SPECIALISTS, SPECIALIST_REGISTRY


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, id_: str, name: str, input_: dict[str, Any]) -> None:
        self.id = id_
        self.name = name
        self.input = input_


class _FinalMsg:
    usage = None

    def __init__(self, content: list[Any], stop_reason: str) -> None:
        self.content = content
        self.stop_reason = stop_reason


class _Delta:
    type = "text_delta"

    def __init__(self, text: str) -> None:
        self.text = text


class _DeltaEvent:
    type = "content_block_delta"

    def __init__(self, text: str) -> None:
        self.delta = _Delta(text)


class _FakeStream:
    """Unlike test_specialist_tool_choice.py's copy of this class, this one
    DOES stream the final message's text blocks as content_block_delta
    events -- _stream_agent_loop only accumulates/yields text from those
    delta events, never from final_msg.content directly, so a harness that
    skips deltas (correct for tests that only assert on tool_choice/tool_use)
    would silently make every response text "" here, masking the exact gap
    this file exists to catch.
    """

    def __init__(self, final_msg: _FinalMsg) -> None:
        self._final_msg = final_msg
        self._deltas = [
            _DeltaEvent(b.text)
            for b in final_msg.content
            if getattr(b, "type", None) == "text"
        ]

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> Any:
        if self._deltas:
            return self._deltas.pop(0)
        raise StopAsyncIteration

    async def get_final_message(self) -> _FinalMsg:
        return self._final_msg


class _ScriptedProvider:
    def __init__(self, final_msgs: list[_FinalMsg]) -> None:
        self._final_msgs = list(final_msgs)
        self.calls: list[dict[str, Any]] = []

    def messages_stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream(self._final_msgs.pop(0))


@pytest.fixture()
def audit(tmp_path: Path):
    """A temp-dir audit logger that the global `log_event` will route to.

    Mirrors test_executive_response_audit.py's fixture -- real SQLite in a
    temp dir, not a mock, so the narration_policy_violation assertions below
    exercise the actual audit_log write path.
    """
    instance = AuditLogger(tmp_path / "audit.db")
    set_audit_logger(instance)
    yield instance
    set_audit_logger(None)


def _run_loop_with_collector(
    provider: _ScriptedProvider,
    user_message: str,
    collector: DebugCollector,
    *,
    real_dispatch: bool = False,
    patch_retrieval_only: bool = False,
    is_committee_draft: bool = False,
) -> str:
    """``real_dispatch=True`` patches the low-level dispatch/retrieval
    helpers *router.py calls*, not ``route_parallel`` itself -- so
    ``route_parallel``'s own ``debug_collector.emit("specialist_start"/
    "specialist_done", ...)`` calls execute for real, exactly as they would
    in production. Patching ``route_parallel`` wholesale (as
    test_specialist_tool_choice.py does -- it only asserts on tool_choice,
    never on debug events) would silently skip those emits.

    ``patch_retrieval_only=True`` patches just the retrieval/prefetch
    helpers (no network/ChromaDB dependency in a unit test) but leaves
    ``route_to_specialist`` itself real -- for exercising its actual
    "Unknown specialist: ..." fallback on a bogus/missing specialist name,
    which is pure Python and makes no external call.
    """

    async def _go() -> str:
        text = ""
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "openexecutive.orchestrator.executive.get_provider",
                    return_value=provider,
                )
            )
            if real_dispatch:
                stack.enter_context(
                    patch(
                        "openexecutive.orchestrator.router.route_to_specialist",
                        return_value="mocked specialist output",
                    )
                )
            if real_dispatch or patch_retrieval_only:
                stack.enter_context(
                    patch(
                        "openexecutive.orchestrator.router._retrieve_for_call",
                        return_value="",
                    )
                )
                stack.enter_context(
                    patch(
                        "openexecutive.orchestrator.router._retrieve_failures_for_call",
                        return_value="",
                    )
                )
                stack.enter_context(
                    patch(
                        "openexecutive.orchestrator.router._prefetch_department_for_call",
                        return_value="",
                    )
                )
            async for item in Executive()._stream_agent_loop(
                system_blocks=[],
                messages=[{"role": "user", "content": user_message}],
                model="claude-test",
                user_message=user_message,
                debug_collector=collector,
                turn_id=collector.turn_id,
                is_committee_draft=is_committee_draft,
            ):
                if isinstance(item, str) and item != Executive._THINKING:
                    text += item
        return text

    return asyncio.run(_go())


def _assert_claims_are_backed(
    response_text: str, collector: DebugCollector, audit: AuditLogger
) -> None:
    """A consultation claim must be backed either by a real specialist
    dispatch this turn, or -- since the runtime guard is log-only and never
    prevents the fabrication itself -- by a narration_policy_violation audit
    row recording that the claim was caught.
    """
    claims_consultation = bool(find_consultation_claim(response_text))
    if not claims_consultation:
        return
    logged_specialist_events = [
        e for e in collector._events if e.kind in ("specialist_start", "specialist_done")
    ]
    if logged_specialist_events:
        return  # genuinely backed by a real consult this turn
    # AuditLogger.query() has no turn_id filter param -- filter client-side.
    violations = [
        e
        for e in audit.query(event_type="narration_policy_violation")
        if e.turn_id == collector.turn_id
    ]
    assert violations, (
        "Response claims a specialist was consulted "
        f"({response_text!r}) but no specialist_start/specialist_done log "
        "entry exists to back it, AND no narration_policy_violation audit "
        "row was recorded either -- the narration prohibition in "
        "executive_persona.py ('Consulting Your Team' / 'Do not narrate "
        "your reasoning process') has no runtime detection."
    )


def test_fabricated_consultation_language_has_no_backing_log_entry(
    audit: AuditLogger,
) -> None:
    """The response still ships unmodified (this guard is log-only), but the
    fabrication must now be caught and recorded as a narration_policy_violation
    audit row -- see narration_integrity.narration_policy_violation.
    """
    fabricated_text = (
        "After checking with our CFO, I'd hold off on the raise until the "
        "model firms up."
    )
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock(fabricated_text)], stop_reason="end_turn")]
    )
    collector = DebugCollector(turn_id="audit-fabricated")

    response_text = _run_loop_with_collector(
        provider, "What's your gut read on timing here?", collector
    )

    assert response_text == fabricated_text, (
        "the guard must never modify the response text -- log-only, no blocking"
    )
    _assert_claims_are_backed(response_text, collector, audit)

    violations = [
        e
        for e in audit.query(event_type="narration_policy_violation")
        if e.turn_id == "audit-fabricated"
    ]
    assert len(violations) == 1
    assert violations[0].details.get("matched_phrase")
    assert violations[0].details.get("phase") == "final", (
        "a normal (non-committee) turn's violation row must be phase='final' "
        "-- this loop's own output IS the text delivered to the user"
    )


def test_plain_response_with_no_consultation_claim_passes(audit: AuditLogger) -> None:
    """Control: an honest, claim-free response never trips the check."""
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock("I'd hold off until the model firms up.")], stop_reason="end_turn")]
    )
    collector = DebugCollector(turn_id="audit-honest")

    response_text = _run_loop_with_collector(
        provider, "What's your gut read on timing here?", collector
    )

    _assert_claims_are_backed(response_text, collector, audit)  # must not raise
    assert not audit.query(event_type="narration_policy_violation")


def test_consultation_language_with_real_backing_log_does_not_fail(
    audit: AuditLogger,
) -> None:
    """Control: a genuine consult (real specialist_start/specialist_done) backs the claim."""
    provider = _ScriptedProvider(
        [
            _FinalMsg(
                [
                    _ToolUseBlock(
                        "tu-1", "consult_specialist", {"specialist": "cfo", "query": "runway"}
                    )
                ],
                stop_reason="tool_use",
            ),
            _FinalMsg(
                [_TextBlock("After checking with our CFO, hold off on the raise.")],
                stop_reason="end_turn",
            ),
        ]
    )
    collector = DebugCollector(turn_id="audit-backed")

    response_text = _run_loop_with_collector(
        provider,
        "What should our runway look like before the next fundraising round?",
        collector,
        real_dispatch=True,
    )

    _assert_claims_are_backed(response_text, collector, audit)  # must not raise
    # Genuinely backed -- the guard must not also log a violation.
    assert not audit.query(event_type="narration_policy_violation")


def test_bogus_specialist_name_does_not_suppress_detection(audit: AuditLogger) -> None:
    """Regression coverage for a gap an adversarial review round caught: a
    consult_specialist call naming a specialist NOT in SPECIALIST_REGISTRY
    (e.g. a non-Anthropic model not honoring the tool schema's enum, or a
    malformed call) makes route_to_specialist return an "Unknown
    specialist: ..." string rather than raising. Before the fix,
    specialists_consulted was extended unconditionally from every dispatched
    call, so this bogus call alone made specialists_consulted truthy and
    permanently disabled narration_policy_violation for the rest of the
    turn -- despite zero real specialist analysis having occurred. The fix
    filters specialists_consulted to real SPECIALIST_REGISTRY keys.

    Uses patch_retrieval_only (route_to_specialist runs for real -- its
    "Unknown specialist" fallback is pure Python, no network call) so this
    exercises the actual code path the gap was in, not a mock standing in
    for it.
    """
    provider = _ScriptedProvider(
        [
            _FinalMsg(
                [
                    _ToolUseBlock(
                        "tu-1",
                        "consult_specialist",
                        {"specialist": "not_a_real_specialist", "query": "runway"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FinalMsg(
                [_TextBlock("After checking with our CFO, hold off on the raise.")],
                stop_reason="end_turn",
            ),
        ]
    )
    collector = DebugCollector(turn_id="audit-bogus-specialist")

    response_text = _run_loop_with_collector(
        provider,
        "What should our runway look like before the next fundraising round?",
        collector,
        patch_retrieval_only=True,
    )

    # No real specialist_start/specialist_done -- SPECIALIST_REGISTRY.get()
    # returned None for the bogus name, so route_parallel never reached a
    # real specialist's analyze().
    logged_specialist_events = [
        e for e in collector._events if e.kind in ("specialist_start", "specialist_done")
    ]
    assert logged_specialist_events, (
        "specialist_start/specialist_done still fire even for a bogus "
        "specialist name -- route_parallel's own emits aren't gated on "
        "registry validity, only specialists_consulted is"
    )
    violations = [
        e
        for e in audit.query(event_type="narration_policy_violation")
        if e.turn_id == "audit-bogus-specialist"
    ]
    assert violations, (
        f"Response claims a specialist was consulted ({response_text!r}) and "
        "the only dispatched call named a specialist outside "
        "SPECIALIST_REGISTRY -- narration_policy_violation must still fire, "
        "not be silently suppressed by the bogus call counting as 'consulted'"
    )


def test_triage_chat_consult_does_not_suppress_detection(audit: AuditLogger) -> None:
    """Issue #1's second symptom, distinct from the bogus-name case above:
    "triage" IS a real `SPECIALIST_REGISTRY` member (unlike
    "not_a_real_specialist"), so the OLD raw-registry-membership filter in
    executive.py's `really_consulted` -- the exact fix that closed the
    bogus-name gap -- did NOT catch it. Nothing stopped a forced chat consult
    from naming `specialist="triage"`, despite triage being meta-routing for
    the alert pipeline (agents/triage.py + alerts/pipeline.py), not a domain
    specialist. Before this test's fix, `really_consulted` used
    `SPECIALIST_REGISTRY` membership, so this call alone made
    `specialists_consulted` truthy and permanently suppressed
    narration_policy_violation for the rest of the turn -- despite
    route_to_specialist having rejected the call and run zero real analysis.
    The fix tightens `really_consulted` to `CHAT_CONSULTABLE_SPECIALISTS`
    (SPECIALIST_REGISTRY minus "triage"), the same allowed set
    route_to_specialist itself now validates against.

    Uses patch_retrieval_only (route_to_specialist runs for real -- its
    triage-rejection fallback is pure Python, no network call) so this
    exercises the actual code path the gap was in, not a mock standing in
    for it.
    """
    provider = _ScriptedProvider(
        [
            _FinalMsg(
                [
                    _ToolUseBlock(
                        "tu-1",
                        "consult_specialist",
                        {"specialist": "triage", "query": "runway"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FinalMsg(
                [_TextBlock("After checking with our CFO, hold off on the raise.")],
                stop_reason="end_turn",
            ),
        ]
    )
    collector = DebugCollector(turn_id="audit-triage-specialist")

    response_text = _run_loop_with_collector(
        provider,
        "What should our runway look like before the next fundraising round?",
        collector,
        patch_retrieval_only=True,
    )

    # route_parallel's specialist_start/specialist_done emits aren't gated on
    # validity, so they still fire -- same as the bogus-name case.
    logged_specialist_events = [
        e for e in collector._events if e.kind in ("specialist_start", "specialist_done")
    ]
    assert logged_specialist_events, (
        "specialist_start/specialist_done still fire even for a rejected "
        "'triage' consult -- route_parallel's own emits aren't gated on "
        "chat-consultability, only specialists_consulted is"
    )
    violations = [
        e
        for e in audit.query(event_type="narration_policy_violation")
        if e.turn_id == "audit-triage-specialist"
    ]
    assert violations, (
        f"Response claims a specialist was consulted ({response_text!r}) and "
        "the only dispatched call named 'triage' -- a real SPECIALIST_REGISTRY "
        "member but not chat-consultable -- narration_policy_violation must "
        "still fire, not be silently suppressed by the rejected call still "
        "counting as 'consulted'"
    )


def test_fabricated_claim_about_uninvoked_specialist_is_flagged_despite_real_consult(
    audit: AuditLogger,
) -> None:
    """Issue #2 regression: the detector used to treat ``specialists_consulted``
    as a single turn-level boolean, so a genuine consult to ANY specialist
    this turn emptied out the check for the rest of the response -- a
    fabricated claim naming a completely different, never-dispatched
    specialist in the same text went undetected. Here the turn genuinely
    consults `cfo` (`real_dispatch=True` -- real specialist_start/
    specialist_done via `route_parallel` and executive.py's real
    `really_consulted` filter; `route_to_specialist` itself is mocked, same
    as `test_consultation_language_with_real_backing_log_does_not_fail`'s
    pattern for a legitimate consult) AND the response text fabricates a
    second claim naming `ciso`, which was never dispatched. The real cfo
    consult must back only the cfo claim; the ciso claim must still be
    independently checked against `specialists_consulted` and flagged.
    """
    provider = _ScriptedProvider(
        [
            _FinalMsg(
                [
                    _ToolUseBlock(
                        "tu-1", "consult_specialist", {"specialist": "cfo", "query": "runway"}
                    )
                ],
                stop_reason="tool_use",
            ),
            _FinalMsg(
                [
                    _TextBlock(
                        "After checking with our CFO on runway, I'm comfortable "
                        "with the plan. Separately, our CISO confirmed the "
                        "incident from last week is fully contained."
                    )
                ],
                stop_reason="end_turn",
            ),
        ]
    )
    collector = DebugCollector(turn_id="audit-mixed-real-and-fabricated")

    response_text = _run_loop_with_collector(
        provider,
        "Are we good on runway, and is that incident closed out?",
        collector,
        real_dispatch=True,
    )

    violations = [
        e
        for e in audit.query(event_type="narration_policy_violation")
        if e.turn_id == "audit-mixed-real-and-fabricated"
    ]
    assert violations, (
        f"Response ({response_text!r}) fabricates a CISO consult that never "
        "happened this turn (only cfo was actually dispatched) -- the real "
        "cfo consult must not suppress detection of the fabricated CISO "
        "claim (the exact false negative Issue #2 reports)"
    )
    assert "ciso" in violations[0].details.get("matched_phrase", "").lower(), (
        "the flagged claim must be the CISO one, not the genuinely-backed "
        "CFO claim -- backing is per-specialist, not per-turn"
    )


def test_committee_draft_fabrication_is_tagged_phase_committee_draft(
    audit: AuditLogger,
) -> None:
    """A fabricated claim in the committee DRAFT pass (is_committee_draft=True)
    must be recorded with details.phase == "committee_draft", not "final" --
    that draft text is never shown to the user (stream_chat_with_committee
    discards it and ships a separately-generated revision instead), so a
    reader must be able to tell this row apart from one describing text that
    actually reached the user without an indirect join against
    committee_review rows on the same turn_id.
    """
    fabricated_text = (
        "After checking with our CFO, I'd hold off on the raise until the "
        "model firms up."
    )
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock(fabricated_text)], stop_reason="end_turn")]
    )
    collector = DebugCollector(turn_id="audit-committee-draft")

    _run_loop_with_collector(
        provider,
        "What's your gut read on timing here?",
        collector,
        is_committee_draft=True,
    )

    violations = [
        e
        for e in audit.query(event_type="narration_policy_violation")
        if e.turn_id == "audit-committee-draft"
    ]
    assert violations, "expected a violation row for the fabricated committee draft"
    assert violations[0].details.get("phase") == "committee_draft"


def test_multi_specialist_claim_in_one_sentence_requires_all_named_backed() -> None:
    """Found by adversarial review: an earlier version of
    `_resolve_claimed_specialists` (then singular, `_resolve_claimed_specialist`)
    took only the FIRST alias found in a claim's window, so a single claim
    naming two specialists ("I consulted our CFO and our CISO on this") let a
    real `cfo` consult silently back a fabricated `ciso` claim riding along
    in the same sentence -- reopening Issue #2's exact false negative under a
    conjunction instead of a sentence break. A claim naming multiple
    specialists must be backed only when ALL of them were really dispatched.
    """
    text = "I consulted our CFO and our CISO on this."
    assert narration_policy_violation(text, ["cfo"]) is not None, (
        "ciso is named but was never dispatched -- must still be flagged "
        "even though cfo (also named) genuinely was"
    )
    assert narration_policy_violation(text, ["cfo", "ciso"]) is None, (
        "both named specialists were genuinely dispatched -- fully backed"
    )
    assert narration_policy_violation(text, []) is not None


def test_ambiguous_security_word_falls_back_to_turn_level() -> None:
    """'security' appears in this detector's claim vocabulary (e.g.
    "consulted our security team") but doesn't map to a single
    SPECIALIST_REGISTRY key -- it's plausibly ciso, cyberops, or grc. A claim
    naming only "security" must be treated as unnamed (turn-level backing:
    flagged only when nothing was consulted at all), not guessed at.
    """
    text = "I consulted our security team on this."
    assert narration_policy_violation(text, ["cfo"]) is None, (
        "unnamed (security doesn't resolve) + a real consult elsewhere this "
        "turn -- backed under the turn-level fallback"
    )
    assert narration_policy_violation(text, []) is not None


def test_sentence_boundary_stops_alias_resolution_from_a_later_claim() -> None:
    """An alias in a later, unrelated sentence must not be attributed to an
    earlier unnamed claim -- `_resolve_claimed_specialists` stops scanning at
    the first sentence terminator after the match specifically to prevent
    this. Without that boundary, the CISO mention below would incorrectly
    make the first (genuinely unnamed) claim resolve as a CISO claim.
    """
    text = "I have consulted extensively before deciding. Our CISO is out this week."
    assert narration_policy_violation(text, []) is not None, (
        "no real consult at all this turn -- the unnamed first claim is "
        "unbacked regardless of the unrelated CISO mention two sentences later"
    )
    assert narration_policy_violation(text, ["cfo"]) is None, (
        "a real (unrelated) consult backs the unnamed claim under the "
        "turn-level fallback -- the CISO mention must not leak into this "
        "claim's window and force a specific, unbacked identity onto it"
    )


def test_specialist_aliases_match_chat_consultable_registry() -> None:
    """Drift guard for _ALIAS_TO_SPECIALIST_KEY, replacing the runtime filter
    narration_integrity.py deliberately no longer applies (see its comment):
    every alias must resolve to a real, chat-consultable specialist. A
    typo'd or stale alias pointing at a key that's absent from
    SPECIALIST_REGISTRY (or present but not chat-consultable, e.g. a future
    `triage`-like meta-routing entry) would make any claim naming it
    permanently unbackable instead of correctly checked -- silently
    over-flagging rather than crashing, so nothing else would catch it.
    """
    for alias, key in _ALIAS_TO_SPECIALIST_KEY.items():
        assert key in SPECIALIST_REGISTRY, f"alias {alias!r} maps to unknown key {key!r}"
        assert key in CHAT_CONSULTABLE_SPECIALISTS, (
            f"alias {alias!r} maps to {key!r}, which is not chat-consultable -- "
            "specialists_consulted can never contain it, so a claim naming "
            "this alias could never be backed"
        )


def test_newline_stops_alias_resolution_across_markdown_bullets() -> None:
    """Found by round-2 adversarial review: LLM chat output is markdown-heavy
    and list items routinely carry no terminal `.`/`!`/`?` at all, so an
    80-char lookahead window bounded only by sentence punctuation walks
    straight across the line break into the NEXT bullet's claim -- a new
    false positive in exactly the shape the sentence boundary exists to
    prevent. A real cfo consult must still back an unnamed claim in one
    bullet even though a later, unrelated bullet happens to name gc (legal).
    """
    text = (
        "Here's where we landed:\n\n"
        "- I consulted the modeling work already done\n"
        "- Legal review is still outstanding\n"
    )
    assert narration_policy_violation(text, ["cfo"]) is None, (
        "the first bullet's unnamed claim is backed by the real cfo consult "
        "under the turn-level fallback -- 'legal' from the SECOND, unrelated "
        "bullet must not leak across the newline and force a specific, "
        "unbacked identity onto the first claim"
    )
    assert narration_policy_violation(text, []) is not None, (
        "control: with nothing consulted at all this turn, the first "
        "bullet's unnamed claim is still correctly flagged"
    )


def test_lookahead_cap_does_not_scan_a_mid_word_truncation() -> None:
    """Found by round-2 adversarial review: slicing the lookahead tail to a
    raw _MAX_LOOKAHEAD_CHARS character count can cut a word in half, and
    whichever fragment survives at the end of the slice can coincidentally
    satisfy a `\\b...\\b` alias match it has no business matching (e.g. a
    cut that lands right after "...product" out of "...products", with the
    plural's "s" falling just past the cap). The window must be trimmed back
    to a whole-token boundary before the alias scan runs, so truncation can
    only ever miss an alias split across the cap -- never fabricate one.
    """
    padding = "x" * 71
    text = f"I consulted {padding} products are fine."
    assert narration_policy_violation(text, ["cfo"]) is None, (
        "no named specialist should resolve from a truncation artifact -- "
        "the claim must fall back to turn-level backing (and be backed by "
        "the real cfo consult), not spuriously resolve as a 'product' claim"
    )


def test_specialist_aliases_cover_every_domain_word_the_patterns_recognize() -> None:
    """The opposite direction of the drift guard above (found by round-2
    adversarial review): a domain word present in _CONSULTATION_CLAIM_PATTERNS
    but MISSING from _ALIAS_TO_SPECIALIST_KEY would silently downgrade any
    claim naming it to "unnamed," making it suppressible by an unrelated real
    consult -- the same bug shape the alias-filter removal fixed one layer up,
    just approached from a coverage gap instead of a bad mapping. "security"
    is the sole, deliberate exception (see module docstring: it's ambiguous
    across ciso/cyberops/grc, so it's intentionally left unaliased).

    This does NOT assert every SPECIALIST_REGISTRY member has an alias --
    only ones the claim patterns' own vocabulary already names (e.g.
    board_comms/cso/talent have no informal word in that vocabulary at all,
    so there's nothing for an alias to cover there; that's accepted scope,
    not a gap -- see the module docstring's "Deliberately narrower than the
    full registry" note).
    """
    domain_words_in_patterns = {
        "cfo", "ciso", "cyberops", "grc", "legal", "security",
        "hr", "marketing", "product", "operations",
    }
    aliased_or_deliberately_excluded = set(_ALIAS_TO_SPECIALIST_KEY) | {"security"}
    assert domain_words_in_patterns == aliased_or_deliberately_excluded, (
        "pattern vocabulary and alias coverage have drifted -- symmetric "
        f"difference: {domain_words_in_patterns ^ aliased_or_deliberately_excluded}"
    )
