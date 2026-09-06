"""Detects narrated-consultation language with no backing specialist dispatch.

executive_persona.py's rules ("Never reference your internal architecture...
no mention of specialists" / "Do not narrate your reasoning process... do
not say 'let me check'") are prompt-only instructions -- nothing inspects
the model's own output text against what was actually dispatched via
`consult_specialist`. This module is that check.

Observability only. Callers must never use this to block, retry, or modify
a response -- it exists purely to log a `narration_policy_violation` audit
row so real data on false-positive rates can inform whether runtime
enforcement is ever warranted. Ordinary advice-giving phrasing ("you should
check with the legal team before signing") can and will match; that is an
accepted, unquantified risk this signal exists to measure, not eliminate.

Seeded from the informal detector in
tests/unit/test_narration_consultation_integrity.py, hardened past its
known tense/aspect gaps -- e.g. it matched "consulting our CFO team" but not
"consulted our CFO team", "I consulted" but not "I've consulted" or "we
consulted", and "checking/checked with our" but not the bare/future form
"check with our". Still not exhaustive: paraphrases that avoid
"consult"/"check" entirely (e.g. "after talking with our CFO") aren't
covered, and "check in with" (a different phrasal verb) still isn't.

Known scope limits (found by adversarial review, accepted rather than fixed
in the PR that added this -- see architecture-facts.yaml's `audit` section
for the disclosure):

- **Turn-level, not per-specialist.** `specialists_consulted` is "was ANY
  specialist dispatched this turn," not "was the specific specialist named
  in the claim actually dispatched." A response that genuinely consults
  `cfo` but then also fabricates "our CISO confirmed this is fine" in the
  same turn will NOT be flagged -- the real `cfo` dispatch alone empties out
  the check. Matching this narrowly to real dispatch would require entity
  resolution against the claim text, which is a materially different (and
  more false-positive-prone) detector than the one built here.
- **Forced-consult turns are mostly self-suppressing by design.**
  `_stream_agent_loop` forces `consult_specialist` by default on-topic
  (`force_specialist_consult`), so on a typical on-topic turn
  `specialists_consulted` is non-empty before this check ever runs its
  regex. In practice this check's live trigger surface is narrower than its
  name suggests: off-topic-classified turns, non-forced later iterations,
  and turns where a forced tool_choice still somehow produced no dispatch.
- **On committee turns, this runs against the draft, not the delivered
  text.** `stream_chat_with_committee` calls `_stream_agent_loop` only to
  produce an internal draft that is never shown to the user; the actual
  delivered text comes from a separate revision call outside the loop that
  this check never sees. So a `narration_policy_violation` row with
  `details["phase"] == "committee_draft"` describes the discarded draft,
  not what shipped -- it can fire on draft narration the revision later
  removed (false positive relative to what the user saw), and it will miss
  narration the revision introduces (false negative; still uncovered).
  `executive.py` threads `is_committee_draft` through
  `_stream_agent_loop`/`_check_narration_integrity` specifically so a row
  can be told apart from one on `phase == "final"` (every other caller,
  where the checked text IS what the user received) without an indirect
  join against `committee_review` rows on the same `turn_id`.
- **Cross-iteration accumulation catches non-specialist tool preambles
  too.** The response text checked is the whole turn's accumulated text
  (every iteration's streamed text, not just the final one) so that a
  fabricated claim uttered as preamble before a forced specialist call is
  still caught. The cost: preamble before a genuine skill/MCP tool call
  (e.g. "let me check with the calendar" before a calendar tool call) can
  also match, producing a violation row for a turn with zero specialist
  calls at all. Accepted for the same reason as the advice-giving false
  positives above -- this is a log-only signal meant to surface real
  volume/shape data, not a precision-tuned classifier.
"""
from __future__ import annotations

import re

_CONSULTATION_CLAIM_PATTERNS = [
    # First-person/collective claim of having consulted -- bare, -s, -ed,
    # -ing, and perfect-aspect ("have/has/'ve consulted").
    r"\b(?:I|we)(?:'ve|\s+(?:have|has))?\s+consult(?:ed|ing|s)?\b",
    r"\bafter\s+(?:I\s+|we\s+)?consult(?:ed|ing)\b",
    # Subject-agnostic "check(s/ed/ing) with our/the" -- deliberately also
    # catches advice-giving phrasing; see module docstring.
    r"\bcheck(?:s|ed|ing)?\s+with\s+(?:our|the)\b",
    r"\bconsult(?:ed|ing|s)?\s+(?:our|with)\s+(?:the\s+)?"
    r"(?:cfo|ciso|cyberops|grc|legal|security|hr|marketing|product|operations)\s+team\b",
    r"\bour\s+(?:cfo|ciso|cyberops team|security team|legal team|hr team)\s+"
    r"(?:said|advised|confirmed)\b",
    r"\blet me (?:consult|check with|bring in)\b",
    r"\bbrought in (?:our|the)\b",
]
CONSULTATION_CLAIM_RE = re.compile("|".join(_CONSULTATION_CLAIM_PATTERNS), re.IGNORECASE)


def find_consultation_claim(response_text: str) -> str | None:
    """Return the matched phrase if ``response_text`` narrates a specialist
    consult having occurred, else ``None``."""
    match = CONSULTATION_CLAIM_RE.search(response_text)
    return match.group(0) if match else None


def narration_policy_violation(
    response_text: str, specialists_consulted: list[str]
) -> str | None:
    """Return the matched claim phrase if ``response_text`` claims a
    consult occurred but ``specialists_consulted`` (this turn's real
    dispatches) is empty. Returns ``None`` when the claim is backed by a
    real consult, or when there's no claim at all.
    """
    if specialists_consulted:
        return None
    return find_consultation_claim(response_text)
