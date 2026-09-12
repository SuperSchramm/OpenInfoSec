---
name: third-party-vendor-risk-assessment
description: Assess and quantify the security risk a vendor, contractor, or cloud partner introduces before or during a business relationship
when_to_use: A new vendor is being onboarded, an existing vendor relationship is under review, or a vendor-related incident or finding needs to be scoped
category: security
---

# Third-Party & Vendor Risk Assessment

A vendor's security posture becomes your exposure the moment their system touches your data, your network, or your regulated environment. GRC's job is quantifying that exposure before it becomes an incident — not just collecting a vendor's compliance attestations and filing them.

## Inputs to gather first

Before assessing a vendor relationship, confirm or ask for:

1. **What the vendor actually touches** — data types, system access, network connectivity, physical access — not what the contract says they're allowed to touch, what they actually do
2. **The vendor's own compliance posture** — relevant certifications (SOC 2, ISO 27001), attestations, and how current they are
3. **Criticality of the relationship** — what breaks, and how badly, if this vendor is compromised or simply unavailable
4. **Existing contractual protections** — data handling terms, breach notification obligations, right-to-audit clauses, liability allocation
5. **Whether this vendor has subprocessors or its own third parties** — risk doesn't stop at the first vendor relationship

## The assessment sequence

1. **Scope actual access before scoring risk.** A vendor with read-only access to non-sensitive data carries different risk than one with administrative access to a production environment, regardless of how similar their marketing materials sound. Score the access, not the vendor category.
2. **Verify compliance claims rather than accepting them at face value.** A SOC 2 report or ISO certification confirms an audit occurred at a point in time — read the actual report (particularly any noted exceptions or qualifications), don't just confirm the certification exists.
3. **Assess criticality independent of compliance posture.** A perfectly compliant vendor providing a mission-critical service still represents concentration risk if there's no viable alternative or continuity plan if they fail.
4. **Check contractual protections match the actual risk.** A vendor handling regulated data needs contractual breach-notification timelines that align with your own regulatory obligations — a generic vendor contract's default terms often don't.
5. **Extend the assessment to known subprocessors where the relationship is high-risk.** A vendor's own vendor can be the actual point of failure — this doesn't require auditing every subprocessor, but for high-criticality relationships, confirm the primary vendor's subprocessor oversight is real, not just contractually promised.
6. **Set a re-assessment cadence proportional to criticality**, not a flat annual review for every vendor regardless of risk tier.

## Where this connects to other GRC and CISO work

A vendor risk finding that reveals genuine, uncontained exposure is a CISO-level input for board risk reporting (see `board-risk-reporting.md`) — supply chain dependency risk is one of that skill's own translation categories. A vendor compliance gap that's real but low-urgency follows the same classification logic as `compliance-gap-risk-assessment.md`: distinguish a documentation shortfall from an actual capability gap in the vendor's own controls before deciding how urgently to act.

## Common failure modes

- **Treating certification as proof of security rather than evidence of an audit.** A certification confirms a point-in-time audit against a defined scope — it doesn't confirm current operational reality, and it doesn't confirm the scope covered what your relationship with them actually touches.
- **Scoring risk by vendor size or reputation rather than actual access.** A well-known, large vendor with broad access to sensitive systems is not automatically lower-risk than a small vendor with narrow, well-controlled access.
- **Ignoring subprocessor risk on high-criticality relationships.** The primary vendor's own security may be strong while a subprocessor they rely on is the actual weak point.
- **Applying a flat review cadence regardless of criticality.** A low-risk, low-access vendor doesn't need the same annual-review rigor as one holding regulated data or critical-system access — but the reverse mistake (under-reviewing a critical vendor) is the more dangerous failure mode.
