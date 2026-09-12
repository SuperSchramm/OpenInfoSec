---
name: incident-escalation-framework
description: Determine when a security incident must escalate from SecOps to CISO to executive/board/regulatory level
when_to_use: A security incident is active or just contained, and the CISO or CyberOps specialist needs to decide who needs to know and how urgently
category: security
---

# Incident Escalation & Cross-Domain Decision Framework

Every incident starts at the operational level. Most stay there. The CISO's job is knowing precisely when one doesn't — escalating too late creates legal and reputational exposure; escalating too often trains the board to tune out real signal.

## Inputs to gather first

Before deciding an escalation level, confirm or ask for:

1. **What's confirmed vs. suspected** — don't escalate on rumor, but don't wait for full confirmation on a fast-moving incident either
2. **Data scope** — is there evidence or reasonable suspicion of access to regulated data (PHI, PII, cardholder data, sensitive OT/ICS system information)?
3. **Operational impact** — is a primary revenue-generating or safety-critical system affected, and for how long?
4. **Containment status** — is lateral movement ongoing, or has the incident been isolated?
5. **Regulatory clock exposure** — does anything here trigger a statutory notification timeline?

If any of these is genuinely unknown, say so explicitly in the escalation rather than guessing — an escalation built on an unstated assumption is worse than one that flags its own uncertainty.

## The four escalation levels

| Level | Trigger | Decision-makers | Actions |
|---|---|---|---|
| **1 — Operational noise** | Isolated malware block, standard phishing attempt, contained single-endpoint event, no lateral movement | SOC Lead, SecOps team | Standard IR playbook, logging, root cause analysis |
| **2 — High-severity operational incident** | Active credential dumping, unauthorized access to non-sensitive systems, isolated ransomware execution without exfiltration, failure of a redundant critical control | SecOps Manager, Incident Commander | Containment, eradication, forensics activation. CISO notified via daily situational digest — not woken up, not board-notified |
| **3 — CISO escalation (material threat)** | Confirmed or suspected exfiltration of sensitive data; breach of a critical boundary (e.g., IT-to-OT jump); active ransomware reaching enterprise staging; critical third-party compromise affecting core operations | CISO, Head of IR, General Counsel, Privacy Officer | Mobilize executive IR team; initiate legal privilege protocol; engage external retainer (DFIR, PR); start assessing regulatory notification clocks |
| **4 — C-suite / board / regulatory** | Confirmed material impact on operations; unauthorized access to a regulated data store that triggers statutory notification; significant reputational or financial liability | CEO, Board Audit/Risk Committee, CISO, General Counsel | Execute crisis communication plan; file required disclosures; notify regulators; execute public disclosure if required |

## The decision logic, not just the table

A level isn't chosen by pattern-matching the trigger list alone — walk through these four questions explicitly:

1. **Materiality** — does this disrupt primary revenue generation or core operational capacity beyond the defined RTO?
2. **Data compromise scope** — is there evidence or reasonable suspicion of unauthorized access to a regulated dataset?
3. **Legal and regulatory obligations** — does this trigger a mandatory statutory notification timeline?
4. **Systemic contagion** — does containment require disconnecting systems vital to safety, customer operations, or public utility function?

A "yes" on any single question is usually enough to justify moving up a level, even if the trigger-table pattern feels borderline. When in doubt, escalate one level higher and let the receiving level stand it back down — the cost of a false positive at Level 3 is far lower than the cost of a missed Level 4.

## Common failure modes

- **Waiting for full certainty before escalating.** Regulatory notification clocks often start at "reasonable suspicion," not confirmed fact — waiting to escalate until an incident is fully understood can itself become the compliance failure.
- **Escalating on severity alone, ignoring regulatory triggers.** A technically minor incident touching regulated data can still force a Level 3/4 response purely on notification-timeline grounds.
- **Treating Level 2→3 as a technical handoff instead of a legal one.** The moment General Counsel and Privilege protocols enter the picture, communication discipline (what gets written down, how) changes — this isn't just "loop in more people," it changes how the incident should be discussed internally.
