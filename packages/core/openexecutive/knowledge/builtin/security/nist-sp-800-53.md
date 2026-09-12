# NIST SP 800-53: Control Families at a Glance

SP 800-53 is the US federal government's catalog of security and privacy controls — far more granular than CSF 2.0's functional model, and the actual control-level source many compliance mappings (FedRAMP, and by extension a great deal of commercial GRC work) build on. It's organized into families, each addressing a distinct control domain. This summary covers what each family is *for*, not the individual controls within it — treat this as an index, not a substitute for the actual control text when a specific control needs to be cited or implemented.

## Control Families (Summary Level)

| Family | Focus |
|---|---|
| **Access Control (AC)** | Who can do what, on which systems, under what conditions |
| **Awareness and Training (AT)** | Ensuring personnel understand their security responsibilities |
| **Audit and Accountability (AU)** | Logging, log retention, and the ability to reconstruct what happened |
| **Assessment, Authorization, and Monitoring (CA)** | Ongoing verification that controls are actually working, not just documented |
| **Configuration Management (CM)** | Controlling and tracking changes to systems, reducing unauthorized/undocumented drift |
| **Contingency Planning (CP)** | Continuity and disaster recovery capability |
| **Identification and Authentication (IA)** | Verifying identity before granting access |
| **Incident Response (IR)** | Detection, handling, and reporting of security incidents |
| **Maintenance (MA)** | Secure system maintenance practices |
| **Media Protection (MP)** | Protecting data on physical and removable media |
| **Physical and Environmental Protection (PE)** | Physical access and environmental safeguards |
| **Planning (PL)** | Security planning documentation and processes |
| **Program Management (PM)** | Organization-wide security program oversight, distinct from system-level controls |
| **Personnel Security (PS)** | Screening, transfer, and termination procedures tied to access |
| **PII Processing and Transparency (PT)** | Privacy-specific controls around personal information handling |
| **Risk Assessment (RA)** | Identifying and evaluating risk to systems and data |
| **System and Services Acquisition (SA)** | Security requirements built into procurement and development |
| **System and Communications Protection (SC)** | Network and communications security controls |
| **System and Information Integrity (SI)** | Detecting and correcting flaws, malicious code, unauthorized changes |
| **Supply Chain Risk Management (SR)** | Managing risk introduced through suppliers and vendors — the control family most directly relevant to third-party/vendor risk assessment |

Rev. 5 (the current version, published September 2020) organizes 1,196 individual controls across these 20 families — an increase of two families (PT and SR) from Rev. 4's 18.

## Why the Family Structure Matters for GRC Work

When mapping a finding to a framework, working at the family level first (which family does this actually belong to) before drilling into a specific control number reduces misclassification — a finding that looks like an Access Control issue can sometimes actually be an Identification and Authentication issue, and citing the wrong family/control undermines the credibility of the finding when checked.

## Common Misapplications to Flag

- **Citing a specific control number from memory without verifying it against the current revision.** Control numbering and content have changed across revisions (the shift to Rev. 5 restructured and added families, including SR and PT) — a citation that was correct under an older revision may not be current.
- **Treating Program Management (PM) as redundant with system-level controls.** PM controls operate at the organizational level and are frequently the gap that explains why individually-compliant systems still add up to an incoherent overall program.
- **Skipping Supply Chain Risk Management (SR) as a separate consideration from Access Control on vendor accounts.** SR addresses the vendor relationship and supply chain itself, not just the technical access a vendor's systems are granted.

*Note: this is a family-level index, not the control catalog itself — verify exact control language, numbering, and current revision (Rev. 5 as of this writing) against the published NIST SP 800-53 document before citing a specific control in any authored policy language.*
