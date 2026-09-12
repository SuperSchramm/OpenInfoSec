# NIST AI Risk Management Framework (AI RMF): The Four Functions

The AI RMF addresses a different risk surface than CSF or SP 800-53 — it's built for the risks specific to AI systems (bias, explainability, robustness to adversarial input, unpredictable failure modes) rather than traditional confidentiality/integrity/availability risk. It complements, rather than replaces, standard security frameworks for any organization deploying AI systems — an AI system still needs conventional security controls (access, logging, vulnerability management) *and* AI-specific risk management on top of them.

## The Four Functions

| Function | What it covers |
|---|---|
| **Govern** | Establishing a culture of risk management around AI, including policy, accountability, and organizational structure specific to AI risk |
| **Map** | Understanding the context an AI system operates in — its intended use, its actual deployment context, and the risks specific to that context |
| **Measure** | Analyzing, assessing, and tracking AI risks using appropriate methods — including testing for bias, robustness, and performance degradation |
| **Manage** | Prioritizing and acting on the risks identified through Map and Measure, including ongoing monitoring after deployment |

## Why AI Risk Doesn't Reduce to Standard Security Risk

A traditional application either works correctly or has a bug; an AI system can function exactly as designed and still produce a harmful, biased, or incorrect output because the risk lives in the model's behavior across a probability distribution of inputs, not in a discrete code defect. This is why Map and Measure exist as distinct functions from Govern — an organization can have excellent AI governance policy and still fail to actually characterize what a specific model does in its specific deployment context.

## Connection to Existing Organizational Knowledge

This framework is the natural anchor for the existing AI governance taxonomy content already in this knowledge base (`ai_governance_taxonomy.md`) — where that content addresses organizational AI governance structure and maturity levels, AI RMF provides the standard external framework vocabulary for describing the same territory in terms an auditor or regulator would recognize.

## Common Misapplications to Flag

- **Treating AI RMF as a substitute for standard security controls on AI infrastructure.** An AI system still runs on servers, has APIs, processes data, and has all the standard attack surface of any application — AI RMF addresses the *additional* AI-specific risk layer, not a replacement for SP 800-53-style controls on the underlying infrastructure.
- **Doing Govern without Map/Measure.** Policy and accountability structure without actual technical characterization of a specific model's behavior in its specific context produces governance that looks complete on paper but hasn't actually assessed real risk.
- **Treating AI risk assessment as a one-time gate before deployment.** Model behavior can shift as data distributions shift after deployment (model drift) — Measure and Manage are meant to be ongoing, not a pre-launch checkpoint only.

*Note: the AI RMF landscape (including the Generative AI profile, published as a companion resource) has evolved since initial publication — verify current function/category detail against the published NIST AI RMF documentation before treating this summary as complete or current.*
