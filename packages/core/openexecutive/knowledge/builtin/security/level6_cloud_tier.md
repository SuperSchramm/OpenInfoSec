# The Level 6 Cloud Tier: Extending Purdue for Cloud-Connected OT

The Purdue Enterprise Reference Architecture has governed industrial control system security for three decades. Its five-level hierarchy — from field devices up through enterprise IT — is still the shared vocabulary every OT security program uses, and ISA/IEC 62443 and NIST SP 800-82 are both built on top of it. But the model was specified in 1992 for a world of physically isolated control networks and static, purpose-built devices. Only 8.2% of organizations still run a fully air-gapped OT environment (SANS, 2024) — everyone else is operating a Purdue-structured architecture with no formal specification for the cloud connections layered on top of it. That gap has a name: **Level 6, the Cloud Tier.**

## Why a New Level, Not Just "Level 5 Plus Cloud"

Each Purdue level exists because it has a distinct function, a distinct communication pattern, and a distinct trust relationship with the levels around it. Cloud-hosted systems — AI inference platforms, SaaS historians, remote access portals, vendor management tools, MCP servers — check all three boxes. Their functions are distinct from anything at Level 4/5 (bidirectional API calls, session-based remote access, asynchronous data feeds rather than internal enterprise traffic), and their trust relationship with the levels below is both different and, in most environments, dangerously undefined. Treating "the cloud" as an extension of Level 5 rather than its own governed zone is exactly how it ends up with no conduit specification, no security level assignment, and no formal boundary — which is the pattern behind nearly every major OT-adjacent incident of the last decade.

## The Complete Zone Map

| Level | Name | Examples | Primary Concern | Zone Status |
|---|---|---|---|---|
| 0 | Physical / Field | PLCs, RTUs, sensors, actuators, medical devices | Legacy firmware, no auth, physical access | SL-2 minimum |
| 1 | Basic Control | DCS, safety systems, controllers, embedded firmware | Vendor remote access, protocol exploitation, firmware supply chain | SL-2 minimum |
| 2 | Supervisory Control | HMI workstations, engineering stations, clinical workstations | Ransomware staging, credential harvesting | SL-2 to SL-3 |
| 3 | Site / Facility Ops | Historians, MES, ERP integration, SCADA servers | Lateral movement target, AD dependency | SL-2 to SL-3 |
| 3.5 | OT/IT DMZ | Data diodes, jump servers, API gateways | The single most critical enforcement boundary | Explicit allow-list conduit |
| 4 | Enterprise IT | Corporate IT, ERP, email, AD, patch management | Primary ransomware ingress, phishing, AD compromise | SL-1 to SL-2, ZTA identity |
| 5 | Corporate / Internet Gateway | Perimeter firewall, VPN concentrators | Internet-facing attack surface | SL-1, perimeter hardening |
| **6 (proposed)** | **Cloud / Extended Tier** | Cloud historians, SaaS SCADA, AI inference, vendor remote access, MCP servers | Ungoverned access boundary, no prior zone spec, model poisoning | Formal zone: ZTNA + mandatory MFA + conduit spec for every L4/L5 connection |

Level 6 is not a single control — it's a governance requirement that every connection between a cloud-hosted system and an on-premises Purdue level be treated as a formal IEC 62443 conduit, with explicit allow-listing, an authentication requirement, a monitoring obligation, and a security-level assignment, the same way a Level 3.5 conduit is specified today.

## Minimum Controls by Connection Type

| Connection Type | On-Prem Equivalent | Minimum Required Controls |
|---|---|---|
| Cloud remote access / VPN portal | L3.5 jump server / PAM gateway | ZTNA replacing VPN, phishing-resistant MFA, session recording, time-limited least-privilege access |
| Cloud historian / SaaS SCADA | L3 historian server | Data diode or one-way replication where feasible, encrypted transmission, no direct write-back to L0-L2 without explicit conduit approval |
| AI inference endpoint | L3.5 DMZ analytics | Model integrity verification, behavioral baseline monitoring, scoped read-only access, no autonomous write-back to control systems |
| Vendor / OEM remote management | L3.5 jump server | ZTNA + MFA, vendor-specific PAM accounts with no shared credentials, session recording, time-window restrictions |
| MCP server / agentic AI | No prior Purdue equivalent | Agent registry with provenance verification, scoped permissions (no blanket L0-L3 access), human-in-the-loop for any L1/L2 action, behavioral audit trail |

**Why this table earns its keep in practice:** every row maps to a documented failure mode. A VPN portal with no MFA was the entry vector for a ransomware attack that shut down the largest fuel pipeline on the U.S. East Coast in 2021. A remote-access tool with shared credentials let an attacker briefly push sodium hydroxide levels to over 100x safe concentration at a Florida water treatment plant the same month. A cloud portal with no MFA and no segmented monitoring gave an attacker nine undetected days inside a healthcare clearinghouse in 2024, exposing data on 192.7 million people. None of these were zero-days. Each was an ungoverned Level 6 connection that had no formal specification requiring the control that would have stopped it.

## The Core Argument

The Purdue Model's underlying philosophy — separate systems by function, control communication between zones, force an attacker through multiple boundaries — is as valid today as it was in 1992. What breaks down is the *completeness* assumption: that Levels 0 through 5 are the whole picture. They aren't anymore. Formalizing Level 6 doesn't replace anything that already works; it closes the one boundary the original model had no way to see coming.

## Applying This With a Client

- Start by inventorying every cloud connection touching OT, not just the ones IT already knows about. Most organizations have three to five times more Level 6 connections than they believe, accumulated through vendor onboarding and digital transformation with no formal security review.
- Classify each connection against the table above before recommending a control — "add MFA" is not a substitute for identifying which connection type it is and what the full control set requires.
- Treat a Level 6 finding as a zone-governance gap, not an isolated misconfiguration. If one vendor portal has no MFA, assume there are others until the inventory says otherwise.
