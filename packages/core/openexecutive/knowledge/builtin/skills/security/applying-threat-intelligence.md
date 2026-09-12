---
name: applying-threat-intelligence
description: Use threat intelligence to make prioritization, investment, and risk-communication decisions defensible rather than generic
when_to_use: CyberOps needs to justify a prioritization call, a likelihood rating, or a resourcing decision with something more specific than general concern
category: security
---

# Applying Threat Intelligence to Operational Decisions

CTI answers one question: what threats are actually moving toward us, right now, against organizations like ours? Without an answer, every security decision — what to patch first, where to invest, how to staff the SOC — is made in an information vacuum. This skill isn't about running a threat intelligence program; it's about what to do with intelligence once it exists, so it changes a real decision instead of sitting in a feed nobody acts on.

## Inputs to gather first

Before using intelligence to inform a decision, confirm or ask for:

1. **What decision this intelligence needs to support** — a patch-priority call, a board-level likelihood rating, a resourcing or investment ask
2. **Relevant threat actor and TTP data** for the organization's actual sector and region, not just generic industry-wide feeds
3. **Whether the intelligence is corroborated** — a single unconfirmed report carries less weight than multiple independent sources or a sector ISAC advisory
4. **How current the intelligence is** — threat actor behavior shifts; a six-month-old report may no longer reflect current TTPs

## Where CTI actually changes a decision

- **Prioritization stops being guesswork.** A raw vulnerability feed hands you thousands of CVEs. Intelligence tells you which few are being actively weaponized against your industry right now — this is the direct input to the exploitability check in vulnerability prioritization (see `vulnerability-prioritization.md`): a CVE with confirmed active exploitation against your sector moves to the front of the queue regardless of its raw CVSS score.
- **Likelihood ratings become defensible, not aspirational.** "Ransomware is a general concern" doesn't survive a board or auditor question. "This threat actor has hit three organizations in our sector in the past 90 days using this specific TTP" does — because it's checkable and specific.
- **Investment asks get sharper.** A security-investment case (see `security-investment-prioritization.md`) that cites a specific, active threat pattern targeting the exact gap being addressed is a stronger ask than one that cites the gap alone.

## The application sequence

1. **Match the intelligence to the actual decision.** Don't cite a threat report just because it exists — confirm it's relevant to the specific asset, sector, or decision at hand before using it as justification.
2. **Corroborate before treating as fact.** A single source, especially an unconfirmed one, should be stated with appropriate hedging ("reported," "suspected") rather than presented as settled.
3. **Translate the technical indicator into the business or operational consequence.** A TTP description means little to a board; "this is how the threat actor that hit three peer organizations gets in" does.
4. **Re-check currency before reusing intelligence in a new decision.** Threat actor behavior changes; intelligence used to justify last quarter's priority may no longer reflect this quarter's actual risk.

## Where OT/ICS environments require sector-specific intelligence

Threat actors targeting industrial control systems — groups like Volt Typhoon or XENOTIME — operate differently from typical enterprise-targeting actors: they blend into normal operational traffic, persist for extended periods without triggering standard alerting, and target availability and physical safety rather than data theft. Standard enterprise CTI feeds are built to catch data-theft and ransomware patterns and routinely miss this behavior entirely. For any OT or safety-critical environment, sector-specific intelligence — ICS-CERT advisories, the relevant sector ISAC (E-ISAC for energy, H-ISAC for healthcare, and so on), mapped explicitly against the organization's actual architecture — is not optional supplementary reading; it's the only intelligence source actually built to detect this threat pattern.

## Common failure modes

- **Treating intelligence as archaeology instead of input to a live decision.** A threat report that gets read, filed, and never connects to a specific prioritization or investment decision provided no value regardless of its quality.
- **Citing severity without corroboration or currency.** Undermines credibility the first time a cited threat claim is checked and found stale or single-sourced.
- **Relying on generic enterprise feeds for OT/ICS environments.** Produces false confidence — the absence of alerts from a feed not built to detect ICS-targeting behavior is not evidence of absence of that behavior.
- **Skipping the translation step for non-technical audiences.** Intelligence that stays in technical/TTP language never reaches the board or budget conversation it was meant to inform.
