"""Who may see or act on a decision proposal and its briefing alert (issue #51).

A gated decision (e.g. a calendar booking) is visible to, and actionable by, the
principal and the person it was routed to (``approver_person_id``); one with no
approver is the principal's alone. That is the rule the ``/decisions`` routes
enforce (issue #46). The same data also surfaces as a companion briefing alert
(``/today``, the alert-ack route and tool), in the activity feed and behind the
``cancel_calendar_event`` chat tool, and each of those uses the helpers here so
the rule cannot drift between them.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openexecutive.memory.decision_ledger import (
    DECISION_ALERT_SOURCE,
    DECISION_INSTANCE_TAG_PREFIX,
)


def is_decision_alert(alert: Any) -> bool:
    """Whether this alert is the companion of a decision proposal."""
    if getattr(alert, "source", None) == DECISION_ALERT_SOURCE:
        return True
    return any(str(t).startswith(DECISION_INSTANCE_TAG_PREFIX) for t in (alert.topic_tags or []))


def may_see_decision(caller_person_id: int | None, approver_person_id: int | None) -> bool:
    """The principal, or the person the decision was routed to."""
    from openexecutive.people.store import is_principal_or_self

    return is_principal_or_self(caller_person_id, approver_person_id)


def may_handle_alert(caller_person_id: int | None, alert: Any) -> bool:
    """Ordinary alerts are unchanged; a decision alert follows the decision rule
    (its ``routed_to_person_id`` is the approver)."""
    if not is_decision_alert(alert):
        return True
    return may_see_decision(caller_person_id, alert.routed_to_person_id)


def tool_refusal(tool: str, allowed: Callable[[int | None], bool]) -> str | None:
    """The refusal result when this chat turn may not act, else None.

    Needs a verified speaker (``orchestrator.turn_identity``: only the signed-in
    web chat records one), so an inbound email, a channel adapter, the scheduler
    or a workflow -- which record none -- fail closed, like the roster tools."""
    from openexecutive.orchestrator.turn_identity import current_turn_caller

    caller = current_turn_caller.get()
    person_id = getattr(caller, "person_id", None)
    if caller is not None and getattr(caller, "from_web_chat", False):
        try:
            if allowed(person_id):
                return None
        except Exception:
            import logging

            logging.getLogger(__name__).exception("%s: access check failed -- refusing", tool)
    return json.dumps({
        "status": "refused",
        "detail": (
            "That proposal belongs to someone else. Only its owner (the person it was "
            "routed to) or the company's principal can act on it, from the web app."
        ),
    })
