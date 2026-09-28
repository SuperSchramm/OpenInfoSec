"""Issue #50: the persona no longer tells the model roster changes always
succeed (`upsert_person`/`archive_person`/`set_department_head` refuse anyone
but the principal on a verified surface -- see people_tools._refuse_unless_owner)."""
from __future__ import annotations

from openexecutive.prompts.executive_persona import EXECUTIVE_PERSONA_PROMPT


def test_persona_no_longer_claims_roster_tools_never_refuse() -> None:
    assert "Do not refuse and do not tell the user to use the UI" not in EXECUTIVE_PERSONA_PROMPT


def test_persona_tells_the_model_how_to_handle_a_refusal() -> None:
    assert "refuse" in EXECUTIVE_PERSONA_PROMPT
    assert 'status: "refused"' in EXECUTIVE_PERSONA_PROMPT
