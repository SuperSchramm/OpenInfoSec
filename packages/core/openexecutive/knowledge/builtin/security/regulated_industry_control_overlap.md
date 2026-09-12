# Regulated-Industry Detection & Response: Where Frameworks Overlap and Where They Don't

HIPAA, NERC CIP, IEC 62443, GLBA, SOX, and PCI DSS are frequently treated as separate compliance programs requiring separate controls. In practice, most of the detection-and-response capability underneath them is the same capability, built once and mapped multiple times. Building framework-specific programs in parallel is the expensive path; building a reasonably strong general capability and mapping it against each framework's specific language is the efficient one. The exceptions — where a framework genuinely requires something the others don't — matter precisely because they're exceptions, not the default case.

## The Overlap: One Capability, Several Mappings

| Detection/Response Capability | HIPAA | GLBA | SOX | PCI DSS | NERC CIP | IEC 62443 |
|---|---|---|---|---|---|---|
| Centralized logging with defined retention | Required (audit controls, §164.312) | Required (Safeguards Rule monitoring) | Required (control evidence for attestation) | Required (Req. 10) | Required (CIP-007, CIP-008) | Recommended (SL-based logging) |
| Continuous monitoring / anomaly detection | Expected under risk analysis | Required (continuous monitoring or annual pen test) | Required (ongoing control effectiveness) | Required (Req. 10, 11) | Required (CIP-007) | Required (zone/conduit monitoring) |
| Timely incident detection & internal escalation | Required (Security Rule) | Required (event response program) | Required (disclosure controls) | Required (Req. 12) | Required, strict timelines (CIP-008) | Required (incident handling) |
| Access control & identity verification | Required (Security Rule) | Required (MFA mandated) | Required (control environment) | Required (Req. 7, 8) | Required (CIP-004, CIP-005) | Required (identity/authentication) |
| Formal incident response plan, tested | Recommended/expected | Required | Required (as part of internal controls) | Required (Req. 12) | Required (CIP-008) | Required (IR procedures) |

Build each capability once, at whichever framework sets the strictest bar for it, and the weaker requirements from every other applicable framework are typically satisfied as a byproduct — not automatically or without verification, but as the practical default expectation.

## Where the Frameworks Genuinely Diverge

Overlap is the common case, not the universal one. A few divergences matter enough that a shared-capability approach can't paper over them:

- **OT-specific segmentation and asset inventory (NERC CIP, IEC 62443).** Neither HIPAA, GLBA, SOX, nor PCI has a real equivalent to CIP-002's Bulk Electric System asset categorization or IEC 62443's zone-and-conduit model. This isn't a stricter version of an IT control — it's a category of requirement IT-only frameworks don't have at all.
- **SOX's attestation cadence is a governance rhythm, not a technical control.** SOX cares about the reliability and evidentiary trail of the control environment over a reporting period; it's satisfied by *demonstrating* the other capabilities operated consistently, not by any single technical capability itself.
- **HIPAA and PCI's specific data-scope triggers.** What counts as "in scope" — PHI for HIPAA, cardholder data environment for PCI — determines where a shared capability must actually be deployed, not just that it exists somewhere in the enterprise. A capability built for one regulated data type doesn't automatically cover a different one if the scoping boundary wasn't extended to it.
- **NERC CIP's incident reporting timelines are unusually strict relative to the others.** CIP-008 mandatory reporting windows to the E-ISAC are tighter than most breach-notification clocks elsewhere in this table — a shared "we have an incident response plan" capability still needs a framework-specific timeline check layered on top.

## The Practical Application

When evaluating or building a detection-and-response capability for an organization operating under more than one of these frameworks, the question isn't "which framework's version do we build" — it's "what's the strictest requirement across all applicable frameworks for this capability, and does our current implementation meet it." Where a framework has a genuinely distinct requirement (OT segmentation, SOX attestation, scope-specific coverage, an unusually tight timeline), treat that as an addition to the shared baseline, not evidence the shared-baseline approach doesn't work.

## Common Misapplications to Flag

- **Building parallel, framework-specific programs instead of one capability with multiple mappings.** This is the most common source of wasted security spend in multi-regulated organizations — duplicated logging systems, duplicated incident-response plans, duplicated monitoring tooling, each built to satisfy one framework in isolation.
- **Assuming overlap means equivalence.** A capability satisfying HIPAA's audit-control requirement is not automatically satisfying NERC CIP's stricter, OT-specific version of a similar-sounding requirement — verify against each framework's actual text, don't assume the mapping table above is a substitute for that verification.
- **Treating OT-specific requirements as an extension of IT requirements.** NERC CIP and IEC 62443's OT-native requirements exist because OT has a different risk model (safety, availability-over-confidentiality) — not because OT is a stricter version of IT.
