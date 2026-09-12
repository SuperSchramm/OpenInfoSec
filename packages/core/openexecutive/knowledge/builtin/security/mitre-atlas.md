# MITRE ATLAS: ATT&CK's Tactics Adapted for AI Systems

ATLAS (Adversarial Threat Landscape for Artificial-Intelligence Systems) applies ATT&CK's tactic-based structure to attacks that specifically target machine learning systems — a distinct threat category from traditional IT attacks, since the target isn't just infrastructure but the model's training data, its inference behavior, and the integrity of its outputs. An organization deploying AI/ML systems has both the standard ATT&CK-described attack surface (the servers and APIs the model runs on) and this additional, AI-specific attack surface.

## The AI-Specific Additions to the Tactic Sequence

ATLAS defines 16 tactics (versus ATT&CK Enterprise's 14) and roughly 68 top-level techniques. Every standard ATT&CK tactic carries over, including Lateral Movement and Command and Control — ATLAS does not drop these — and the framework adds two tactics with no ATT&CK equivalent:

| AI-Specific Tactic | What it covers |
|---|---|
| **AI Model Access** | Gaining access to the model itself — via a public inference API, a compromised internal endpoint, or direct access to model artifacts — as a distinct objective from gaining access to the underlying infrastructure |
| **AI Attack Staging** | Preparing an attack specifically against the model's behavior — building a proxy/surrogate model, training adversarial data, crafting a data-poisoning payload — before deploying it |

Within these and the standard tactics, ATLAS catalogs AI-specific techniques such as prompt injection, training data poisoning, model extraction (reconstructing a proprietary model through repeated querying), and adversarial examples (inputs deliberately crafted to cause misclassification or unintended behavior).

## Why This Is a Genuinely Different Threat Category

A traditional attacker compromising a database steals or destroys discrete records. An attacker targeting an AI system's training data or inference behavior can cause the system to produce subtly wrong outputs indefinitely, without ever triggering a traditional intrusion-detection signal — the "attack" can look identical to normal system operation from a standard security monitoring perspective, because nothing about the infrastructure itself was compromised.

## Connection to Existing Organizational Knowledge

Like NIST AI RMF, this maps directly onto the existing AI governance taxonomy content — ATLAS provides the attacker's-perspective vocabulary (what an adversary does) as the counterpart to AI RMF's defender's-perspective framework (how an organization manages the risk).

## Common Misapplications to Flag

- **Assuming standard security monitoring covers AI-specific attack techniques.** Prompt injection, model extraction, and data poisoning generally don't trigger conventional network or endpoint detection — they require AI-system-specific monitoring designed for this threat category.
- **Treating AI system security as solved once standard infrastructure controls (from ATT&CK/SP 800-53) are in place.** Those controls protect the infrastructure the model runs on; they don't address attacks against the model's behavior or training data specifically.
- **Underestimating the difficulty of detecting a successful AI-specific attack.** Because these attacks often don't produce anomalous infrastructure behavior, detection frequently depends on monitoring the model's output quality and behavior over time, not just the systems around it.

*Note: ATLAS is a newer, actively evolving framework relative to ATT&CK — verify current tactic/technique detail against the live MITRE ATLAS knowledge base before treating this summary as complete or current.*
