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

Per-specialist backing (Issue #2). A claim is checked against the specific
specialist(s) it names, not against "was ANY specialist dispatched this
turn": a response that genuinely consults `cfo` but also fabricates "our
CISO confirmed this is fine" in the same turn is now flagged for the CISO
claim, even though the real `cfo` dispatch would have emptied out the old
turn-level check. This holds even when both names appear in the same claim
("I consulted our CFO and our CISO on this") -- a claim is backed only if
*every* specialist it names was actually dispatched, not just one of them.
See `_resolve_claimed_specialists` for how a claim's named specialist(s) are
identified, and its docstring for the residual scope limits of that
heuristic (sentence-scoped window, fixed alias vocabulary, no identity
resolution for claims that don't name a specialist at all).

Known scope limits (found by adversarial review, accepted rather than fixed
in the PR that added this -- see architecture-facts.yaml's `audit` section
for the disclosure of the original turn-level gap this module's per-specialist
check now closes):

- **Unnamed claims stay turn-level.** A claim that doesn't name a specific
  specialist (a bare "I have consulted", "let me check with", "brought in
  our") has no identity to check against, so it falls back to the original
  rule: flagged only when NO real consult happened at all this turn. This is
  a deliberate scope limit, not an oversight -- resolving an unnamed claim's
  intended target would require the same entity-resolution machinery this
  module's docstring has long rejected as "a materially different (and more
  false-positive-prone) detector than the one built here."
- **"security" doesn't resolve to one specialist.** The word appears in this
  detector's claim vocabulary (e.g. "consulted our security team") but maps
  to no single registry key -- it's plausibly `ciso`, `cyberops`, or `grc`.
  A claim naming only "security" is treated as unnamed (see above) rather
  than guessed at.
- **Forced-consult turns now only self-suppress UNNAMED claims.**
  `_stream_agent_loop` forces `consult_specialist` by default on-topic
  (`force_specialist_consult`), so on a typical on-topic turn
  `specialists_consulted` is non-empty before this check ever runs its
  regex. Before per-specialist backing, that alone silenced the whole check
  for the rest of the turn -- now it only silences claims that don't name an
  identifiable specialist. A NAMED claim (e.g. "you should check with the
  legal team", "our CISO confirmed...") is checked against that specific
  specialist regardless of what else was dispatched, so this check's live
  trigger surface is WIDER than it used to be for named claims: ordinary
  advice-giving phrasing that happens to name a domain the turn didn't
  consult (see the advice-giving caveat above) will now log on a normal,
  successfully-dispatched on-topic turn, not just on off-topic/undispatched
  ones as before.
- **A naive `.`/`!`/`?` sentence boundary can cut a named claim off early.**
  An abbreviation, decimal, or ellipsis between the claim phrase and the
  specialist name (e.g. "I checked with our Sr. CISO about this") truncates
  the lookahead window before the alias is reached, so the claim resolves as
  unnamed and falls back to turn-level backing. This fails toward the
  pre-fix behavior (backed by any real consult this turn), not toward a new
  false positive -- accepted for the same reason as the module's other
  regex-based imprecision.
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

# Informal domain words this detector's own claim vocabulary already uses
# (see the word lists in _CONSULTATION_CLAIM_PATTERNS above) mapped to their
# SPECIALIST_REGISTRY key. Deliberately narrower than the full registry --
# only words the claim patterns themselves recognize, so this can't claim to
# resolve identity the surrounding regex was never built to notice in the
# first place. "security" is intentionally absent (see module docstring).
#
# Deliberately NOT filtered against CHAT_CONSULTABLE_SPECIALISTS. An earlier
# version of this dict dropped any alias whose key wasn't chat-consultable,
# reasoning that a stale/bad alias shouldn't "claim backing is possible" for
# a specialist that could never really be dispatched -- but that reasoning
# was backwards: dropping the alias downgrades the claim to unnamed, which
# makes it fall back to turn-level backing and become suppressible by any
# unrelated real consult -- the exact bug shape
# test_triage_chat_consult_does_not_suppress_detection guards against, one
# layer up. Keeping the alias is strictly safer: `specialists_consulted`
# (see executive.py's `really_consulted`) can never contain a
# non-chat-consultable key, so a claim naming one is correctly unbackable by
# construction -- no filtering needed. See
# test_specialist_aliases_match_chat_consultable_registry for the drift
# guard this used to provide, now enforced as a test instead.
_ALIAS_TO_SPECIALIST_KEY = {
    "cfo": "cfo",
    "ciso": "ciso",
    "cyberops": "cyberops",
    "grc": "grc",
    "legal": "gc",
    "hr": "chro",
    "marketing": "cmo",
    "product": "cpo",
    "operations": "coo",
}
_SPECIALIST_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(alias) for alias in _ALIAS_TO_SPECIALIST_KEY) + r")\b",
    re.IGNORECASE,
)

# Hard cap on how far past a claim match to scan for a named specialist,
# in case a sentence runs on with no terminator at all.
_MAX_LOOKAHEAD_CHARS = 80


def find_consultation_claim(response_text: str) -> str | None:
    """Return the matched phrase if ``response_text`` narrates a specialist
    consult having occurred, else ``None``."""
    match = CONSULTATION_CLAIM_RE.search(response_text)
    return match.group(0) if match else None


def _resolve_claimed_specialists(response_text: str, match: re.Match[str]) -> set[str]:
    """Best-effort identification of every specialist a matched consultation
    claim names, by scanning from the match forward to the end of its
    sentence/line (or ``_MAX_LOOKAHEAD_CHARS``, whichever comes first) for
    known aliases (``_ALIAS_TO_SPECIALIST_KEY``).

    Returns every alias found, not just the first: a single claim can name
    more than one specialist ("I consulted our CFO and our CISO on this"),
    and a claim naming two specialists is backed only if BOTH were really
    dispatched -- collapsing to the first match alone would let a real `cfo`
    consult silently back a fabricated `ciso` claim riding along in the same
    sentence, reopening Issue #2's false negative under a different phrasing.

    Stopping at the sentence/line boundary keeps an alias in a *later*,
    unrelated sentence -- or the next markdown bullet -- from being
    attributed to an earlier unnamed claim (e.g. "I have consulted
    extensively before deciding. Our CISO is out this week." must not read
    as a CISO claim; nor must "- I consulted the modeling work\n- Legal
    review is outstanding" read the first bullet as naming `gc`). Newline is
    included as a terminator alongside `.!?` specifically because LLM chat
    output is markdown-heavy and list items routinely carry no terminal
    punctuation at all -- without it, the lookahead window walks straight
    across the line break into the next bullet's claim. Scanning forward
    only (never before the match) mirrors how every existing claim pattern
    is built: when a pattern names a domain at all, the domain word follows
    the verb phrase, it never precedes it.

    The ``_MAX_LOOKAHEAD_CHARS`` cap is applied to a whole-token boundary,
    not a raw character cut -- trimming back to the last run of whitespace
    in the capped tail avoids ever scanning a word truncated mid-token, which
    could otherwise coincidentally form (or destroy) a real alias depending
    on exactly where the cut fell.

    Returns the canonical ``SPECIALIST_REGISTRY`` keys named, or an empty set
    when the claim doesn't name an identifiable specialist -- callers must
    fall back to turn-level backing for those (see module docstring).
    """
    tail = response_text[match.end():match.end() + _MAX_LOOKAHEAD_CHARS]
    if len(tail) == _MAX_LOOKAHEAD_CHARS:
        # The cap may have landed mid-word; trim back to the last full
        # token boundary so we only ever scan complete words.
        trimmed = re.match(r"^.*(?=\s)", tail, re.DOTALL)
        tail = trimmed.group(0) if trimmed else ""
    terminator = re.search(r"[.!?\n]", tail)
    lookahead = terminator.start() if terminator else len(tail)
    window = response_text[match.start():match.end() + lookahead]
    return {
        _ALIAS_TO_SPECIALIST_KEY[alias_match.group(1).lower()]
        for alias_match in _SPECIALIST_ALIAS_RE.finditer(window)
    }


def narration_policy_violation(
    response_text: str, specialists_consulted: list[str]
) -> str | None:
    """Return the matched claim phrase for the first consultation claim in
    ``response_text`` that isn't backed by this turn's real dispatches, or
    ``None`` when every claim is backed (or there's no claim at all).

    Backing is checked per-specialist, not per-turn: a claim that names
    specific specialist(s) (see ``_resolve_claimed_specialists``) is backed
    only if EVERY specialist it names appears in ``specialists_consulted``
    -- a real consult to one named specialist does not back a claim that
    also names a different, undispatched one. A claim that names no
    identifiable specialist falls back to turn-level backing: it's flagged
    only when ``specialists_consulted`` is empty, i.e. nothing was genuinely
    dispatched at all this turn.
    """
    consulted = set(specialists_consulted)
    for match in CONSULTATION_CLAIM_RE.finditer(response_text):
        claimed_specialists = _resolve_claimed_specialists(response_text, match)
        if claimed_specialists:
            if not claimed_specialists.issubset(consulted):
                return match.group(0)
            continue
        if not consulted:
            return match.group(0)
    return None
