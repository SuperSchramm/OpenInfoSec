"""Issue #50: `current_turn_caller` is reset when a chat turn ends, rather than
relying only on the request's task ending.

Nothing in this codebase today runs after `event_generator` in the same task
(no `background=` on the chat route's `StreamingResponse`, no `asyncio.create_task`
in `orchestrator/`), so there is no live path where a stale caller currently leaks
into later work -- this is defense-in-depth against one being added later, not a
fix for an active leak. Verified two ways: the contextvars set/reset pattern chat.py
uses restores the prior value correctly, and a source check that the `finally`
block chat.py relies on for that is still there (so a refactor can't silently drop
it without this test noticing).
"""
from __future__ import annotations

import inspect

from openexecutive.api.routes import chat as chat_route
from openexecutive.orchestrator.turn_identity import TurnCaller, current_turn_caller


def test_set_then_reset_restores_the_prior_value() -> None:
    assert current_turn_caller.get() is None
    outer_token = current_turn_caller.set(TurnCaller(person_id=1, verified=True))
    try:
        assert current_turn_caller.get() == TurnCaller(person_id=1, verified=True)
        inner_token = current_turn_caller.set(TurnCaller(person_id=2, verified=True))
        try:
            assert current_turn_caller.get() == TurnCaller(person_id=2, verified=True)
        finally:
            current_turn_caller.reset(inner_token)
        # A nested turn's reset restores the outer turn's caller, not None.
        assert current_turn_caller.get() == TurnCaller(person_id=1, verified=True)
    finally:
        current_turn_caller.reset(outer_token)
    assert current_turn_caller.get() is None


def test_event_generator_source_sets_and_resets_the_caller_per_turn() -> None:
    """Source-level regression guard: the `finally: ... reset(...)` chat.py's
    generator relies on is present and paired with the `.set()` that starts it."""
    source = inspect.getsource(chat_route)
    assert "turn_caller_token = current_turn_caller.set(" in source
    assert "finally:\n            current_turn_caller.reset(turn_caller_token)" in source
