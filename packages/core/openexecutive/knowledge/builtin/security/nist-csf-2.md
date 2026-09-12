# NIST Cybersecurity Framework 2.0: The Six Functions

CSF 2.0 organizes cybersecurity outcomes into six functions. It is deliberately outcome-based, not prescriptive — it describes *what* a mature program achieves, not *how* to achieve it, which is why it maps cleanly onto other frameworks (ISO 27001, SOC 2, sector-specific regulations) rather than competing with them. Treat it as the organizing vocabulary for describing a security program's maturity, not a checklist to complete once.

## The Six Functions

| Function | What it covers | Board-relevant framing |
|---|---|---|
| **Govern** | Establishing and monitoring the organization's cybersecurity risk management strategy, roles, policy, and oversight | Is there a clear owner, a defined risk appetite, and board-level visibility into cybersecurity as a business risk — not just a technical one |
| **Identify** | Understanding the organization's assets, data, systems, and the risks to them | Do we actually know what we have and what depends on it — most other functions fail quietly when this one is incomplete |
| **Protect** | Safeguards to ensure delivery of critical services (access control, awareness training, data security, platform security) | The preventive controls — the ones that reduce likelihood |
| **Detect** | Activities to identify a cybersecurity event as it's happening | The ones that reduce dwell time — how long an incident runs before anyone notices |
| **Respond** | Actions taken once an incident is detected (this is where the containment-triage skill's procedures apply) | Does the organization act quickly and correctly once something is found |
| **Recover** | Restoring capabilities and services impaired by an incident | How fast and cleanly does the organization return to normal operation |

CSF 2.0 contains 22 categories and 106 subcategories distributed across the six functions. Govern alone accounts for 6 categories and 31 subcategories — the single largest share of any function, reflecting how much of the 2.0 update's expansion is governance-focused rather than purely technical.

**Govern was elevated to a standalone, central function in CSF 2.0** (it existed more implicitly under Identify in CSF 1.1) — reflecting a broader shift toward treating cybersecurity governance as a leadership and organizational responsibility, not a subset of technical asset management. This mirrors the CISO's own stated role: setting direction is not the same activity as identifying assets or executing controls.

## Why This Maps Well Onto Board Reporting

Each function suggests a different kind of question for a board update: Govern asks "who owns this and is it prioritized correctly," Identify/Protect ask "are we reducing likelihood," Detect/Respond ask "how fast do we notice and act," Recover asks "how resilient are we to actually failing." A board narrative that only ever reports on Protect (patching, controls) while never addressing Detect/Respond/Recover maturity gives an incomplete picture of actual resilience.

## Common Misapplications to Flag

- **Treating CSF as a compliance checklist rather than a maturity model.** CSF doesn't certify compliance the way a specific regulation does — it's a common vocabulary for describing where a program stands and where it should invest next.
- **Over-indexing on Protect at the expense of Detect/Respond/Recover.** A program that's all prevention and no detection capability looks strong until prevention fails — and it eventually does.
- **Treating Govern as a paperwork function rather than the connective tissue for the other five.** Weak governance is frequently the actual root cause behind gaps that surface elsewhere (see: Equifax's patch-policy-existed-but-didn't-execute failure).

*Note: verify current sub-category detail and exact wording against the published NIST CSF 2.0 document before using this as anything beyond a functional-level orientation — this summary is intentionally high-level and does not enumerate the full category/subcategory structure.*
