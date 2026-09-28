"""Issue #53: Telegram is verified only when BOTH a webhook secret is
configured (an unconfigured webhook accepts any POST, so
`telegram_webhook()`'s `X-Telegram-Bot-Api-Secret-Token` check -- the only
thing standing between an arbitrary POST and this code -- never runs) AND the
chat is a private 1:1 chat (`chat_id > 0`; group/channel ids are negative). A
Person's `telegram_chat_id` could itself be a group, in which case ANY member
of that group resolves to them via `find_person_by_telegram_chat_id` -- and a
group's Session is shared, so trusting it would also let another rostered
member's message in that shared history steer a verified turn.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openexecutive.integrations.telegram_bot import _process_and_reply
from openexecutive.orchestrator.turn_identity import TurnCaller, current_turn_caller


@pytest.mark.asyncio
async def test_verified_when_webhook_secret_is_configured() -> None:
    seen: list[TurnCaller | None] = []

    async def _capture(**_kw: Any) -> str:
        seen.append(current_turn_caller.get())
        return "ok"

    with (
        patch("openexecutive.onboarding.profile_builder.load_or_create_profile") as mock_profile,
        patch("openexecutive.people.store.find_person_by_telegram_chat_id", return_value=MagicMock(id=7)),
        patch("openexecutive.alerts.pipeline.schedule_evaluation"),
        patch("openexecutive.workflows.inbound_resolver.resolve_inbound_message", new=AsyncMock(return_value=None)),
        patch("openexecutive.knowledge.retriever.retrieve", return_value=""),
        patch("openexecutive.memory.episodic.format_for_prompt", return_value=""),
        patch("openexecutive.orchestrator.executive.Executive") as MockExec,
        patch("openexecutive.orchestrator.mcp_gateway.get_active_gateway", return_value=None),
        patch("openexecutive.audit.log_event"),
        patch("openexecutive.memory.session_store.load_messages", return_value=[]),
        patch("openexecutive.memory.session_store.create_session"),
        patch("openexecutive.memory.session_store.save_message"),
        patch("openexecutive.memory.session_store.update_session_timestamp"),
        patch("openexecutive.integrations.telegram_bot.send_message", new=AsyncMock()),
        patch(
            "openexecutive.integrations.telegram_bot.get_settings",
            return_value=MagicMock(telegram_webhook_secret="s3cr3t"),
        ),
    ):
        mock_profile.return_value.is_empty.return_value = True
        mock_exec_instance = MagicMock()
        mock_exec_instance.chat = AsyncMock(side_effect=_capture)
        MockExec.return_value = mock_exec_instance

        assert current_turn_caller.get() is None
        await _process_and_reply("hi", "Alex", chat_id=555, message_id=1, token="tok")
        assert current_turn_caller.get() is None  # reset after, same as web chat

    assert seen == [TurnCaller(person_id=7, verified=True, surface="telegram")]


@pytest.mark.asyncio
async def test_not_verified_when_no_webhook_secret_is_configured() -> None:
    """Without a secret, `telegram_webhook()`'s own check never runs, so a POST
    with any chat_id reaches here regardless of who really sent it -- the roster
    tools and decision-proposal gates must not trust it."""
    seen: list[TurnCaller | None] = []

    async def _capture(**_kw: Any) -> str:
        seen.append(current_turn_caller.get())
        return "ok"

    with (
        patch("openexecutive.onboarding.profile_builder.load_or_create_profile") as mock_profile,
        patch("openexecutive.people.store.find_person_by_telegram_chat_id", return_value=MagicMock(id=7)),
        patch("openexecutive.alerts.pipeline.schedule_evaluation"),
        patch("openexecutive.workflows.inbound_resolver.resolve_inbound_message", new=AsyncMock(return_value=None)),
        patch("openexecutive.knowledge.retriever.retrieve", return_value=""),
        patch("openexecutive.memory.episodic.format_for_prompt", return_value=""),
        patch("openexecutive.orchestrator.executive.Executive") as MockExec,
        patch("openexecutive.orchestrator.mcp_gateway.get_active_gateway", return_value=None),
        patch("openexecutive.audit.log_event"),
        patch("openexecutive.memory.session_store.load_messages", return_value=[]),
        patch("openexecutive.memory.session_store.create_session"),
        patch("openexecutive.memory.session_store.save_message"),
        patch("openexecutive.memory.session_store.update_session_timestamp"),
        patch("openexecutive.integrations.telegram_bot.send_message", new=AsyncMock()),
        patch(
            "openexecutive.integrations.telegram_bot.get_settings",
            return_value=MagicMock(telegram_webhook_secret=""),
        ),
    ):
        mock_profile.return_value.is_empty.return_value = True
        mock_exec_instance = MagicMock()
        mock_exec_instance.chat = AsyncMock(side_effect=_capture)
        MockExec.return_value = mock_exec_instance

        await _process_and_reply("hi", "Alex", chat_id=555, message_id=1, token="tok")

    assert seen == [TurnCaller(person_id=7, verified=False, surface="telegram")]

@pytest.mark.asyncio
async def test_not_verified_for_a_group_chat_even_with_the_secret_configured() -> None:
    """A negative chat_id is a group/channel, not a private 1:1 chat."""
    seen: list[TurnCaller | None] = []

    async def _capture(**_kw: Any) -> str:
        seen.append(current_turn_caller.get())
        return "ok"

    with (
        patch("openexecutive.onboarding.profile_builder.load_or_create_profile") as mock_profile,
        patch("openexecutive.people.store.find_person_by_telegram_chat_id", return_value=MagicMock(id=7)),
        patch("openexecutive.alerts.pipeline.schedule_evaluation"),
        patch("openexecutive.workflows.inbound_resolver.resolve_inbound_message", new=AsyncMock(return_value=None)),
        patch("openexecutive.knowledge.retriever.retrieve", return_value=""),
        patch("openexecutive.memory.episodic.format_for_prompt", return_value=""),
        patch("openexecutive.orchestrator.executive.Executive") as MockExec,
        patch("openexecutive.orchestrator.mcp_gateway.get_active_gateway", return_value=None),
        patch("openexecutive.audit.log_event"),
        patch("openexecutive.memory.session_store.load_messages", return_value=[]),
        patch("openexecutive.memory.session_store.create_session"),
        patch("openexecutive.memory.session_store.save_message"),
        patch("openexecutive.memory.session_store.update_session_timestamp"),
        patch("openexecutive.integrations.telegram_bot.send_message", new=AsyncMock()),
        patch(
            "openexecutive.integrations.telegram_bot.get_settings",
            return_value=MagicMock(telegram_webhook_secret="s3cr3t"),
        ),
    ):
        mock_profile.return_value.is_empty.return_value = True
        mock_exec_instance = MagicMock()
        mock_exec_instance.chat = AsyncMock(side_effect=_capture)
        MockExec.return_value = mock_exec_instance

        await _process_and_reply("hi", "Group", chat_id=-100555, message_id=1, token="tok")

    assert seen == [TurnCaller(person_id=7, verified=False, surface="telegram")]
