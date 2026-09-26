"""Who is asking, for the current chat turn (roster-write gate).

The Executive's roster tools (``upsert_person``, ``archive_person``,
``set_department_head``) change who may sign in, who the Executive may email and
who approves what, and they are offered on every turn -- including one an inbound
email, a Google Chat message or a teammate started. ``Session`` carries no caller
or surface, so an entry point that has VERIFIED who is speaking records it here,
per request, in a context variable (like ``schedule_tools.current_session``):
never on the shared ``Session``, whose object two people can use in turn.

The default is "unknown", so anything that does not set it -- channel adapters,
the scheduler, workflows, alert review, the CLI, the MCP server -- fails closed.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnCaller:
    """The verified speaker of one turn."""

    person_id: int | None  # the roster Person the caller resolved to (None: not on the roster)
    from_web_chat: bool  # the turn came through the signed-in web chat (the UI proxy stamps the caller)


# Set inside the chat stream (like current_session, without a reset: the
# request's own context ends with the request).
current_turn_caller: contextvars.ContextVar[TurnCaller | None] = contextvars.ContextVar(
    "current_turn_caller", default=None
)
