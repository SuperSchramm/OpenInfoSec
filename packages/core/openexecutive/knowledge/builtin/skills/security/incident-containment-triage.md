---
name: incident-containment-triage
description: Triage and contain an active security incident — assess scope, choose a containment action, and avoid the most common containment mistakes
when_to_use: An incident is confirmed or strongly suspected and CyberOps needs to decide what to isolate, disable, or disconnect right now
category: security
---

# Incident Containment & Triage

Containment is a bet: you're trading some amount of operational disruption now against the risk of the incident spreading if you wait. The goal of triage is making that trade deliberately, with the actual scope in view, rather than reflexively pulling the first plug you find.

## Inputs to gather first

Before choosing a containment action, confirm or ask for:

1. **What's confirmed vs. suspected** — don't contain based on rumor, but don't wait for full confirmation on something actively spreading either
2. **Scope so far** — how many hosts/accounts/systems show evidence of compromise, and is that number still growing
3. **What the affected system does** — is it isolated, does it sit on a path to something more sensitive, does taking it offline create its own consequence
4. **Evidence preservation needs** — will the chosen containment action destroy forensic value (a reboot clears volatile memory; a full disk wipe clears everything)
5. **Whether the affected system is safety-critical or patient/worker-connected** — if so, disconnecting it is not automatically the safe default (see below)

## The triage sequence

1. **Confirm scope before acting broadly.** A single-host, single-account incident and a spreading, multi-host incident call for different containment radii. Acting as if every incident is the worst case wastes response capacity and unnecessarily disrupts operations; acting as if every incident is minor risks letting a spreading one grow.
2. **Choose the least-disruptive containment action that actually stops the spread.** Options in roughly increasing order of disruption: disable a compromised account, isolate a host at the network layer (not necessarily power it off), block a malicious destination at the firewall, segment a subnet, take a system fully offline. Start as low on this list as the evidence supports, and escalate the containment action if the incident keeps spreading despite it.
3. **Preserve evidence before destroying it, where the timeline allows.** A memory capture or forensic image before a reboot or reimage costs minutes and can be the difference between knowing root cause and never knowing it. Skip this only when containment genuinely can't wait — an active, spreading compromise sometimes can't.
4. **Verify containment worked before declaring it contained.** Confirm the spread has actually stopped (no new indicators appearing) rather than assuming the action was sufficient because it was taken.
5. **Communicate scope and status honestly, even when incomplete.** "Contained to 3 hosts, still verifying nothing else is affected" is a legitimate status. Reporting "contained" before that's actually verified creates a false all-clear that undermines trust the next time containment status is reported.

## Where physical safety changes the calculus

For any system connected to a physical process or a patient — industrial control equipment, medical devices, building safety systems — disconnecting or powering off is not automatically the lowest-risk containment action, and can itself be the more dangerous choice. Before isolating such a system:

- Confirm what happens to the physical process or patient if the system loses connectivity or power — some fail safe, some don't
- Involve whoever owns the physical/clinical/operational side of that system in the containment decision, not just security
- Where full isolation carries a safety risk, consider network-layer containment (blocking outbound communication, restricting to a monitored allow-list) over physical disconnection, if that's sufficient to stop the spread

This consideration applies equally to an industrial control system and a hospital's patient-connected medical device — both are OT by the same definition, and both can turn a security containment decision into a safety incident if the physical/clinical consequence of disconnection isn't checked first.

## Common failure modes

- **Reflexive full shutdown before scope is known.** Often destroys the evidence needed to determine root cause and can trigger data loss or availability consequences disproportionate to the actual incident.
- **Treating containment as complete without verification.** New indicators appearing after "containment" usually mean the action taken was insufficient, not that a second, unrelated incident started.
- **Isolating a safety-critical or patient-connected system without checking the physical consequence first.** The security team's instinct to disconnect can, in these environments, cause the harm the response was supposed to prevent.
- **Over-escalating containment radius based on fear rather than evidence.** Taking an entire segment offline for a single-host issue disrupts operations without a corresponding security benefit, and trains the organization to see security response as disproportionate.
