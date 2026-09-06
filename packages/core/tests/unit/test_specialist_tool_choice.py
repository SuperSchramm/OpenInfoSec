"""Coverage for the consult_specialist tool_choice nudge.

Originally `_stream_agent_loop` never set `tool_choice`, so every call
defaulted to Anthropic's "auto" -- the model was always free to skip
`consult_specialist` entirely. The first fix gated a forced
`tool_choice={"type": "tool", "name": "consult_specialist"}` on
`plausibly_on_topic()` (router.py) matching a keyword. That design was
inverted after an audit found it missed real domain-specific queries whose
wording didn't happen to hit a keyword: an OT/shadow-AI incident query
matched zero keywords across all 13 specialists and silently fell back to
unforced "auto" on exactly the domain forcing exists to protect.

Current behavior, on iteration 1 of a fresh turn only:

  - forces `consult_specialist` by DEFAULT,
  - unless `is_off_topic()` matches its narrow allowlist (greetings/small
    talk, meta-questions about the tool itself) -- see router.py's comment
    above `is_off_topic()` for the full rationale (this is a
    security-adjacent advisory tool; the safe failure direction is
    over-forcing, not under-forcing),
  - once the model has made its own tool-use choice (iteration 2+), it is
    never forced again — otherwise synthesis after a consult could never
    finish with a text-only answer.

`plausibly_on_topic()` no longer gates anything here; it's retained purely
as an observability signal (logged per turn) and is still directly unit
tested below for its own contract.

Fake provider plumbing mirrors test_executive_form_patch.py's pattern
(each test file in this suite keeps its own minimal copy rather than
sharing a fixture module).
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import pytest

from openexecutive.orchestrator.executive import Executive
from openexecutive.orchestrator.router import (
    SPECIALIST_KEYWORDS,
    SPECIALIST_REGISTRY,
    plausibly_on_topic,
)


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
    usage = None  # _emit_cache_event no-ops on usage=None

    def __init__(self, content: list[Any], stop_reason: str) -> None:
        self.content = content
        self.stop_reason = stop_reason


class _FakeStream:
    def __init__(self, final_msg: _FinalMsg) -> None:
        self._final_msg = final_msg

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration  # no text deltas; content rides on final_msg

    async def get_final_message(self) -> _FinalMsg:
        return self._final_msg


class _ScriptedProvider:
    """Returns one scripted final message per messages_stream call."""

    def __init__(self, final_msgs: list[_FinalMsg]) -> None:
        self._final_msgs = list(final_msgs)
        self.calls: list[dict[str, Any]] = []

    def messages_stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream(self._final_msgs.pop(0))


def _run_loop(
    provider: _ScriptedProvider, user_message: str, *, mock_route_parallel: bool = False
) -> list[Any]:
    async def _go() -> list[Any]:
        items: list[Any] = []
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "openexecutive.orchestrator.executive.get_provider",
                    return_value=provider,
                )
            )
            if mock_route_parallel:
                stack.enter_context(
                    patch(
                        "openexecutive.orchestrator.executive.route_parallel",
                        return_value=["mocked specialist output"],
                    )
                )
            async for item in Executive()._stream_agent_loop(
                system_blocks=[],
                messages=[{"role": "user", "content": user_message}],
                model="claude-test",
                user_message=user_message,
            ):
                items.append(item)
        return items

    return asyncio.run(_go())


def test_on_topic_message_forces_specialist_tool_choice_on_iteration_one() -> None:
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock("answer")], stop_reason="end_turn")]
    )
    _run_loop(
        provider, "What should our runway look like before the next fundraising round?"
    )
    assert provider.calls[0].get("tool_choice") == {
        "type": "tool",
        "name": "consult_specialist",
    }


def test_off_topic_message_leaves_tool_choice_unforced() -> None:
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock("hey there")], stop_reason="end_turn")]
    )
    _run_loop(provider, "hi, how are you")
    assert "tool_choice" not in provider.calls[0]


def test_meta_question_about_the_tool_leaves_tool_choice_unforced() -> None:
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock("I'm Claude, an AI model by Anthropic.")], stop_reason="end_turn")]
    )
    _run_loop(provider, "What model are you?")
    assert "tool_choice" not in provider.calls[0]


def test_meta_question_lookalike_business_questions_still_force() -> None:
    """Regression coverage for two bugs an adversarial review round caught in
    an earlier version of is_off_topic(): a substring-search meta-question
    regex misclassified genuine on-topic questions as off-topic because they
    happened to contain a matched phrase ("What can you do about X", "Who
    are you Y-ing"). is_off_topic() now requires the ENTIRE normalized
    message to match a curated phrase, so any real additional content -- the
    exact shape of these fixtures -- must fall through to forced.
    """
    messages = [
        "What can you do about the ransomware on our OT segment?",
        "Who are you recommending we hire as our first CISO?",
        "Who are you going to assign to the SOC 2 remediation?",
        "We are being audited next week. What can you do to help us close the gaps?",
        "How does this tool work in our incident response plan?",
    ]
    for message in messages:
        provider = _ScriptedProvider(
            [_FinalMsg([_TextBlock("answer")], stop_reason="end_turn")]
        )
        _run_loop(provider, message)
        assert provider.calls[0].get("tool_choice") == {
            "type": "tool",
            "name": "consult_specialist",
        }, f"expected forced tool_choice for on-topic message: {message!r}"


def test_attachment_augmented_meta_question_still_forces() -> None:
    """Regression coverage for the attachment-poisoning variant of the same
    bug: api/routes/chat.py appends extracted attachment text after the
    user's own words before this ever reaches is_off_topic(). A substring
    search over that combined string meant a boilerplate FAQ line inside an
    uploaded document (e.g. "What can you do if you suspect credentials were
    exposed?") could silently withhold forcing on a real question the user
    asked about the document. Exact-match-on-the-whole-message means any
    attachment content breaks the match by construction.
    """
    message = (
        "What model are you?\n\n"
        "Attached: Q3 vendor security questionnaire.\n"
        "What can you do to protect against SQL injection?\nSection 2..."
    )
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock("answer")], stop_reason="end_turn")]
    )
    _run_loop(provider, message)
    assert provider.calls[0].get("tool_choice") == {
        "type": "tool",
        "name": "consult_specialist",
    }


def test_short_affirmation_and_filler_replies_leave_tool_choice_unforced() -> None:
    """A second adversarial review round found the default-force design has
    a real cost/latency tradeoff, not just a hypothetical one: exact-match
    on the curated allowlist missed extremely common short replies ("yes",
    "no", "sure", "never mind"), so routine conversational filler forced a
    full specialist round. Widened _OFF_TOPIC_PHRASES to cover these -- safe
    to do because exact-match can't be tricked into swallowing real content
    ("yes, and also patch the CVE" still forces; see the companion test
    below).
    """
    from openexecutive.orchestrator.router import is_off_topic

    for message in ["yes", "no", "sure", "never mind", "ok thanks", "👍"]:
        provider = _ScriptedProvider(
            [_FinalMsg([_TextBlock("noted")], stop_reason="end_turn")]
        )
        _run_loop(provider, message)
        assert "tool_choice" not in provider.calls[0], f"expected unforced for: {message!r}"

    # The exact-match design must not generalize past the literal filler --
    # any elaboration on an affirmation is real content and must still force.
    assert not is_off_topic("yes, and also patch the CVE by Friday")
    assert not is_off_topic("no, we need to escalate this to legal")


def test_keyword_free_domain_message_now_forces_by_default() -> None:
    """The specific gap the audit found: a real domain-specific query whose
    wording matches zero keywords across all 13 specialists. Under the old
    plausibly_on_topic()-gated design this silently fell back to unforced
    "auto"; under the new default-force design it must still force.
    """
    message = (
        "Our DMZ monitoring flagged outbound traffic from an HMI/engineering "
        "workstation on the supervisory network that looks like calls to a "
        "public LLM API. Nobody on the OT team says they authorized any AI "
        "tool there."
    )
    assert not plausibly_on_topic(message), (
        "test fixture assumption broken: this message now matches a keyword, "
        "so it no longer demonstrates the gap this test exists to cover"
    )
    provider = _ScriptedProvider(
        [_FinalMsg([_TextBlock("answer")], stop_reason="end_turn")]
    )
    _run_loop(provider, message)
    assert provider.calls[0].get("tool_choice") == {
        "type": "tool",
        "name": "consult_specialist",
    }


def test_forced_tool_choice_does_not_repeat_on_later_iterations() -> None:
    provider = _ScriptedProvider(
        [
            _FinalMsg(
                [
                    _ToolUseBlock(
                        "tu-1",
                        "consult_specialist",
                        {"specialist": "cfo", "query": "runway"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FinalMsg([_TextBlock("final answer")], stop_reason="end_turn"),
        ]
    )
    _run_loop(
        provider,
        "What should our runway look like before the next fundraising round?",
        mock_route_parallel=True,
    )
    assert provider.calls[0].get("tool_choice") == {
        "type": "tool",
        "name": "consult_specialist",
    }
    assert "tool_choice" not in provider.calls[1]


def test_plausibly_on_topic_matches_each_specialist_domain() -> None:
    # One representative phrase per specialist keyword set — every domain,
    # including the three security specialists, must actually trigger.
    for specialist, keywords in SPECIALIST_KEYWORDS.items():
        sample = f"Can you help me think through this: {keywords[0]}?"
        assert plausibly_on_topic(sample), f"{specialist} keyword {keywords[0]!r} did not match"


def test_plausibly_on_topic_false_for_small_talk() -> None:
    for message in ["hi", "thanks, that helps", "good morning", "sounds good"]:
        assert not plausibly_on_topic(message)


@pytest.mark.parametrize("specialist_key", sorted(SPECIALIST_REGISTRY.keys()))
def test_forced_consult_reaches_route_to_specialist_for_every_registry_key(
    specialist_key: str,
) -> None:
    """A consult_specialist tool_use call naming each SPECIALIST_REGISTRY key
    must actually reach router.route_to_specialist with that key -- not just
    satisfy the tool_choice/keyword-classification checks above (none of
    which patch route_to_specialist or inspect what it was called with).

    Patches route_to_specialist itself (the dispatch boundary router.py
    calls into per specialist) rather than route_parallel wholesale -- the
    same real_dispatch pattern proven in
    test_narration_consultation_integrity.py's `_run_loop_with_collector`.
    Mocking route_parallel (as `mock_route_parallel=True` above does for the
    unrelated tool_choice-repeat assertion) would hide a specialist_name
    typo or registry-key mismatch entirely; this proves route_parallel's own
    dispatch loop reaches the right key for all 13 registry entries.

    Includes `triage`: nothing in _stream_agent_loop, route_to_specialist,
    or the consult_specialist tool schema special-cases it (the enum is
    `sorted(SPECIALIST_REGISTRY.keys())`, unfiltered) -- SPECIALIST_KEYWORDS
    omitting triage is an unrelated, separate exclusion (see router.py's
    comment above SPECIALIST_KEYWORDS). So triage gets the same dispatch-
    parity assertion as every other key, not a special "never dispatched"
    claim that the code doesn't actually back.
    """
    provider = _ScriptedProvider(
        [
            _FinalMsg(
                [
                    _ToolUseBlock(
                        "tu-1",
                        "consult_specialist",
                        {"specialist": specialist_key, "query": "test query"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FinalMsg([_TextBlock("final answer")], stop_reason="end_turn"),
        ]
    )
    recorded_calls: list[dict[str, Any]] = []

    async def _fake_route_to_specialist(**kwargs: Any) -> str:
        recorded_calls.append(kwargs)
        return "specialist output"

    async def _go() -> None:
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "openexecutive.orchestrator.executive.get_provider",
                    return_value=provider,
                )
            )
            stack.enter_context(
                patch(
                    "openexecutive.orchestrator.router.route_to_specialist",
                    new=_fake_route_to_specialist,
                )
            )
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
            async for _ in Executive()._stream_agent_loop(
                system_blocks=[],
                messages=[{"role": "user", "content": "test message"}],
                model="claude-test",
                user_message="test message",
            ):
                pass

    asyncio.run(_go())

    assert len(recorded_calls) == 1, (
        f"expected exactly one route_to_specialist call for {specialist_key!r}, "
        f"got {len(recorded_calls)}"
    )
    assert recorded_calls[0]["specialist_name"] == specialist_key
