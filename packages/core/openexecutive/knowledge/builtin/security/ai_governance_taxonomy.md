# AI as a Security Principal: A Four-Class Governance Taxonomy

The Purdue Model's governance logic recognizes exactly two kinds of security principal: humans and devices. AI doesn't fit either category cleanly. An AI agent initiates sessions on its own schedule, consumes data from multiple Purdue levels simultaneously, and in the agentic case, executes actions with no per-transaction human authorization. Treating "AI" as one undifferentiated risk category — the common instinct — produces governance that's either too loose for the highest-risk class or too heavy-handed for the lowest. The fix is to classify AI by *where it sits in the architecture and how much autonomy it has*, and govern each class differently.

## The Four Classes

### Class 1: Embedded OT AI
**What it looks like:** Turbine control optimization algorithms, pipeline pressure optimization, predictive maintenance embedded directly in a PLC, AI algorithms embedded in a medical device.

**Purdue behavior:** Resident at L0-L1. Behaves as a component of the device itself, not as a separate system.

**Primary risk:** Adversarial input, model drift, firmware supply chain compromise — the update mechanism, more than the model, is usually the actual attack surface.

**Required governance:** Firmware-equivalent governance — SBOM, adversarial testing, cryptographic model attestation, a post-market monitoring plan. If the organization already has a device firmware governance program, extend it to cover the embedded model rather than building a parallel process.

### Class 2: On-Premises Analytics AI
**What it looks like:** Historian anomaly detection, process optimization AI running on L3 servers, SCADA predictive analytics, on-premises clinical decision support.

**Purdue behavior:** A data consumer at L3/L3.5, producing recommendations that a human operator acts on.

**Primary risk:** Data exfiltration through the inference API, recommendation poisoning, and — the most common failure in practice — scope creep, where a tool that started with read-only access to one data source quietly accumulates broader access over time with no one revisiting the grant.

**Required governance:** A defined and periodically re-verified data scope, no autonomous write-back to control systems, output attestation, and an explicit operator-in-the-loop requirement for any recommendation that affects a control decision.

### Class 3: Cloud AI / Digital Twin
**What it looks like:** Cloud-hosted predictive maintenance, remote asset monitoring AI, cloud digital twins, population-level analytics platforms.

**Purdue behavior:** Operates at Level 6 (see the Level 6 Cloud Tier framework), bidirectional with L3 and below.

**Primary risk:** OT data exposure to a third party, model poisoning, and the vendor itself as an attack path through the model-update supply chain.

**Required governance:** Full Level 6 governance applies. Contractual security terms equivalent to a BAA/DPA, model-update governance treated the same as device firmware governance, and explicit data sovereignty controls — know which jurisdiction the training and inference data actually lands in.

### Class 4: Agentic / Shadow AI
**What it looks like:** Unsanctioned AI tools operators or technicians are already using for shift reporting or diagnostics, autonomous patch agents, MCP-enabled workflow tools, LLM-integrated SCADA interfaces.

**Purdue behavior:** Unplaced. This is the critical distinction from Classes 1-3 — a Class 4 system bypasses every Purdue level simultaneously rather than sitting in one of them, because its network traffic looks like ordinary operational traffic crossing zones that were never designed to inspect for it.

**Primary risk:** Layer bypass, data exfiltration, privileged action taken with no human authorization, and blast-radius amplification at machine speed rather than human speed.

**Required governance:** An approved tool registry (if it's not on the registry, it's shadow AI by definition), behavioral traffic analysis at the L3.5 DMZ specifically tuned to detect AI-characteristic communication patterns, a production write-access prohibition pending security review, and non-human-identity (NHI) lifecycle governance for anything that does get sanctioned — the same onboarding/offboarding discipline applied to a human account, applied to an agent.

## Why This Isn't a Future Problem

The FDA has already authorized over 1,000 AI-enabled medical devices. Predictive maintenance AI is already deployed across energy and manufacturing OT environments, consuming Level 0 sensor data and returning recommendations that influence Level 1 control decisions. And per IBM's 2025 Cost of a Data Breach report, roughly one in five breaches now involves AI or shadow AI, carrying a documented cost premium of about $670K over a comparable non-AI breach. Every class in this taxonomy is already present in a typical OT environment today — the question is whether it's classified and governed, or just running.

## Applying the Taxonomy

1. **Classify before recommending a control.** "This needs governance" is not actionable until you've placed the system in one of the four classes — the required controls are materially different across them.
2. **Class 4 is the one that's almost always missing from an inventory.** Ask specifically about tools operational staff use informally, not just what's on the approved software list. If no one has looked, assume Class 4 systems exist.
3. **Escalate Class 3 and Class 4 findings to a board-level conversation.** These carry vendor and blast-radius risk that a single team can't fully mitigate on its own — flag it as a risk-register item, not a ticket.
4. **Never let Class 1 or Class 2 autonomy expand without a corresponding governance review.** The most common drift pattern is a Class 2 analytics tool being granted write access "just this once" during an incident and never having that access revoked.
