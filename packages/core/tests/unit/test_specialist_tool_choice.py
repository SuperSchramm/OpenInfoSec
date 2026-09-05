"""Coverage for the consult_specialist tool_choice nudge.

Before this change, `_stream_agent_loop` never set `tool_choice`, so every
call defaulted to Anthropic's "auto" — the model was always free to skip
`consult_specialist` entirely and answer from its own general knowledge,
regardless of provider. `plausibly_on_topic()` (router.py) gates a forced
`tool_choice={"type": "tool", "name": "consult_specialist"}` on iteration 1
of a fresh turn only, so:

  - an on-topic message can't be answered ungrounded on the first pass,
  - a plainly off-topic message (small talk) is untouched,
  - once the model has made its own tool-use choice (iteration 2+), it is
    never forced again — otherwise synthesis after a consult could never
    finish with a text-only answer.

Fake provider plumbing mirrors test_executive_form_patch.py's pattern
(each test file in this suite keeps its own minimal copy rather than
sharing a fixture module).
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from openexecutive.orchestrator.executive import Executive
from openexecutive.orchestrator.router import SPECIALIST_KEYWORDS, plausibly_on_topic


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
