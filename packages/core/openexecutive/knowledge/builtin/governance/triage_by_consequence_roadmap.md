# Triage by Consequence: Sequencing an OT Security Program

The most common governance failure in an OT security program isn't a missing control — it's sequencing by what's easiest to implement instead of by what a ransomware attack would actually hurt most. "Triage by consequence" means protecting operations, safety systems, and any national-security-adjacent dependency first, and letting that priority order — not vendor pressure, not audit convenience — drive the roadmap.

## The Five-Phase Roadmap

**Phase 1 — Discovery (0-12 weeks).** Passive OT asset discovery with no active scanning at L0-L1 (active scanning against legacy control equipment can crash it). Full Level 6 cloud-interface inventory: every cloud connection, vendor portal, remote-access tool, and AI platform touching OT. Operational communication-flow documentation. Legacy device CVE baseline.
*Benchmark:* asset inventory >95% complete; every Level 6 interface mapped; VPN- and portal-class interfaces flagged for MFA enforcement.

**Phase 2 — Level 6 Hardening (12-24 weeks).** ZTNA deployment replacing all VPN. MFA enforcement on every Level 6 interface. Vendor-access PAM implementation. A Level 6 cloud-inventory governance framework. Shadow AI detection at the DMZ.
*Benchmark:* zero ungoverned remote-access interfaces; MFA on all Level 6 human access; vendor session recording operational; a shadow-AI traffic baseline established.

**Phase 3 — L3.5 DMZ Enforcement (24-52 weeks).** Explicit allow-list conduit rules at the DMZ — the highest-leverage single control, given that roughly 70% of OT incidents originate in IT. Active Directory isolation from the OT domain. The AI governance taxonomy applied to every AI platform in scope. CASB for cloud traffic. Data diodes where unidirectional flow is feasible.
*Benchmark:* no implicit trust at the IT/OT boundary; every conduit explicitly specified; AD isolation complete; the AI taxonomy applied across all platforms.

**Phase 4 — L0-L2 Zone Isolation (52-104 weeks).** Zone boundaries enforced per NIST SP 1800-8/1800-10 methodology. Passive behavioral monitoring via digital twin or network TAP — still no active scanning. SBOM-based device procurement going forward. Vendor access to L0-L1 restricted to PAM only. Documented compensating controls for legacy devices that can't be hardened directly.
*Benchmark:* L0-L1 isolated from enterprise AD; all vendor L0-L1 access via PAM; behavioral monitoring operational; SBOM program initiated for new procurement.

**Phase 5 — Strategic Completion (104-156 weeks).** Full ISA/IEC 62443 security-level assignment across every zone. ZTA identity verification at the DMZ per current Cloud Security Alliance OT guidance. A Standing Authority Matrix for pre-authorized, machine-speed containment — every L0-L2 entry in that matrix requires safety-engineering sign-off before approval, since an automated action that interrupts a control loop is a safety event, not a containment success. Quarterly ATT&CK-aligned tabletop exercises with operational staff, not just security staff, in the room.
*Benchmark:* segmented recovery time under 72 hours for critical systems; dwell-time detection target under 15 minutes at the identity layer.

## The Standing Authority Matrix

When an attack moves from initial access to ransomware deployment in under 24 hours — the current median, with roughly 10% of cases under five hours (Secureworks) — a containment decision that requires committee review is a liability, not a control. A Standing Authority Matrix pre-authorizes specific, scoped, reversible containment actions that a SOC can execute at machine speed without waking a director at 3 a.m.

**The one constraint that overrides speed:** any pre-authorized action that could touch an L0-L2 system must be validated against operational safety requirements *before* it goes into the matrix, not discovered during the incident. An isolation action that interrupts a turbine control loop or a pipeline pressure-management system is an operational safety event in its own right — not a successful containment. Get safety engineering sign-off on every L0-L2 entry in the matrix before it's approved, and build it with GC and CIO/COO signatures so the pre-authorization actually holds when it's invoked under pressure.

## Applying This as a Recommendation

1. **Anchor every phase recommendation to what it protects, not what it costs.** "This closes the Colonial Pipeline exposure" lands with a board in a way "this improves our security posture" does not.
2. **Don't let a client skip to Phase 3 or 4 because it feels more technically satisfying.** If Phase 1 discovery isn't complete, later phases are being built on an inventory the team doesn't actually trust.
3. **Treat the timeline bands as sequencing guidance, not a fixed calendar.** A client with a mature Level 6 inventory already in Phase 1 can compress Phase 2; a client with unknown OT/IT AD trust paths should not skip ahead into Phase 3 regardless of calendar pressure.
4. **The Standing Authority Matrix is a Phase 5 deliverable, not a Phase 1 one.** Pre-authorizing machine-speed containment before the underlying segmentation and inventory work is done pre-authorizes actions against an environment nobody has actually mapped yet.
5. **Never recommend a Standing Authority Matrix entry that touches L0-L2 without naming the safety-engineering sign-off requirement in the same breath.** "Machine-speed containment" is the pitch; "safety review before it's pre-authorized" is the non-negotiable half of it — drop one without the other and the recommendation is incomplete regardless of which phase prompted the question.
