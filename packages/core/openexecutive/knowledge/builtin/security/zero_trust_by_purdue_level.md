# Zero Trust by Purdue Level: Where Continuous Verification Helps and Where It Hurts

Zero Trust and the Purdue Model are frequently framed as competitors, and that framing produces bad advice. They aren't solving the same problem. Purdue defines *where* the security boundaries sit. Zero Trust defines *how* you enforce a boundary when something tries to cross it. Applied together, they reinforce each other — but ZTA's continuous-verification requirement introduces latency, and that latency has wildly different consequences depending on where in the hierarchy you apply it. A verification delay that's invisible at the enterprise IT layer can be operationally unacceptable at the device control layer, where timing is a safety parameter, not a performance metric.

## The Differentiated Application

| Purdue Level | ZTA Application | Enforcement Mechanism | Operational Constraint |
|---|---|---|---|
| L0-L1 (Field / Control) | Architectural isolation, not continuous packet verification | Zone boundary enforcement, passive behavioral monitoring via digital twin or network TAP, no direct enterprise-to-device routing | Control-loop timing is measured in milliseconds and cannot absorb per-packet verification overhead — architecture substitutes for real-time verification here |
| L2 (Supervisory) | Full ZTA for all remote and cross-zone access, MFA on all authentication | PAM-governed access, phishing-resistant MFA, application allow-listing, EDR on all workstations | Latency is manageable; this is the highest-value ransomware staging layer, so ZTA is both feasible and essential |
| L3-L3.5 (Operations / DMZ) | Full verification at every boundary crossing | Explicit allow-list conduits only, continuous session monitoring, CASB for cloud traffic, API gateway for all Level 6 connections | This is the single highest-leverage enforcement point in the stack — roughly 70% of OT incidents originate in IT and cross here (Dragos, 2025) |
| L4-L5 (Enterprise IT / Gateway) | Full ZTA per NIST SP 800-207 | FIDO2/passkey MFA, identity threat detection and response, microsegmentation, phishing-resistant email gateway, privileged access workstations | Primary ransomware entry layer across every sector — full ZTA is necessary and operationally feasible here |
| L6 (Cloud) | ZTNA replacing all VPN, cloud-native identity controls | ZTNA with continuous session verification, CASB, API gateway, NHI governance for AI agents and service accounts, mandatory MFA on all human access | Non-negotiable: two of the most consequential OT-adjacent breaches of the last five years entered through a Level 6 interface with no MFA |

## The Decision Rule

Don't ask "should we apply Zero Trust here?" as a yes/no question — ask "what does continuous verification cost at this level, and can the process it's protecting absorb that cost?" At L0-L1, the answer is usually no, and the right substitute is architectural: segment the zone, monitor passively, and don't route directly from enterprise to device. From L2 upward, the answer is close to always yes, with the constraint shifting from "can we afford the latency" to "have we actually deployed it everywhere it's feasible."

## Why the DMZ Is the Priority Layer

If a client can only fully instrument one boundary this quarter, it should be L3.5. Dragos's 2025 OT Cybersecurity Year in Review — drawn from incident data across every critical infrastructure sector in 2024 — found that organizations with enforced IT/OT segmentation had significantly shorter recovery times and avoided paying ransom, while unsegmented organizations saw longer recovery, heavier incident response, and greater exposure to data exfiltration. With roughly 70% of OT-related incidents originating in the IT environment, the L3.5 DMZ is the control point that actually decides whether an IT compromise stays an IT problem.

## Common Misapplications to Flag

- **Applying uniform ZTA policy across all levels.** A single continuous-verification policy written for L4/L5 and rolled out unmodified to L1 either breaks control-loop timing or gets quietly exempted — and an exempted control is worse than an honest architectural decision not to apply it there.
- **Treating Level 6 as an extension of L5 rather than its own ZTNA boundary.** VPN-based access to a Level 6 interface, even with a modern IdP behind it, isn't ZTNA — session-based continuous verification is the actual requirement, not just centralized authentication.
- **Skipping L2 because it "isn't internet-facing."** L2 supervisory workstations are the most common ransomware staging ground precisely because they're treated as internal and lower-priority than the perimeter.
