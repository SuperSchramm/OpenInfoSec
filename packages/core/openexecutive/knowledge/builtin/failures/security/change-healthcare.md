---
domain: security
topic: healthcare_ransomware
company: Change Healthcare (UnitedHealth Group)
year: 2024
failure_type: [ransomware, single_point_of_failure, missing_mfa, patient_safety_impact, healthcare_flat_network]
---

# Change Healthcare Ransomware Attack (2024)

## Situation

Change Healthcare, a subsidiary of UnitedHealth Group, processed an estimated 15 billion healthcare transactions annually — insurance claims, prior authorizations, prescription processing, and payment transactions — functioning as a central clearinghouse connecting a large share of the US healthcare system's providers, pharmacies, and payers. Its scale meant that a disruption to its systems didn't stay contained to one company; it propagated outward to every provider and pharmacy that depended on it to process claims and get paid.

## What Happened

In February 2024, the ALPHV/BlackCat ransomware group compromised Change Healthcare's network and deployed ransomware, forcing the company to take systems offline. Because so much of the US healthcare claims-processing infrastructure ran through Change Healthcare, the outage cascaded nationally: pharmacies couldn't process prescription claims, hospitals and providers couldn't submit or receive payment for claims, and some patients experienced direct delays in care or medication access as a downstream consequence. The outage lasted weeks for full restoration of all services. UnitedHealth Group reported paying a $22 million ransom, and total costs from the incident (response, remediation, and business impact) were estimated well into the billions. Personal and health information belonging to a substantial portion of the US population was ultimately reported as compromised.

## Root Cause

Public reporting and subsequent congressional testimony identified the initial access point as a compromised set of credentials on a server that did not have multi-factor authentication enabled — a basic control gap on a system supporting critical, large-scale infrastructure. Once inside, attackers had the access needed to deploy ransomware broadly. The scale of downstream consequence stemmed less from unusual attacker sophistication than from the sheer centrality of Change Healthcare's role: a single company's security gap became a disruption to patient care access at a national scale, because so much of the industry depended on one point of infrastructure with no readily available alternative.

## Key Decision Failures

- **MFA gap on a system supporting critical national infrastructure**: A basic, widely recommended control was absent on a system whose compromise had consequences far beyond the company itself — the control-to-consequence ratio here was severely mismatched
- **Systemic concentration risk was not treated as a security priority commensurate with its actual blast radius**: The healthcare industry's reliance on a small number of critical clearinghouses meant a single company's incident became an industry-wide, patient-facing disruption — a risk profile that arguably warranted security investment proportional to systemic importance, not just company size
- **Downstream dependency mapping by affected providers and pharmacies was largely absent going into the incident**: Many organizations discovered the depth of their dependency on Change Healthcare only once the outage began, with no tested contingency for claims processing continuing through an alternative path
- **Patient safety and care-access impact was a real, direct consequence, not a theoretical one**: Delays in prescription fulfillment and care access are the healthcare-sector equivalent of the OT safety consequences seen in industrial ransomware incidents — this wasn't a data-only breach

## Lessons

1. **This is the direct healthcare-sector case validating that OT/patient-safety framing isn't limited to industrial control systems.** A ransomware attack on claims-processing infrastructure caused real, reported delays in patient care and medication access — the same category of consequence as an industrial safety incident, arrived at through a purely administrative/IT system rather than a device physically connected to a patient.
2. **Basic control hygiene (MFA) on systems supporting critical, widely-depended-upon infrastructure deserves security investment proportional to systemic blast radius, not just the company's own risk tolerance.** A company whose failure disrupts an entire industry has a different risk calculus than one whose failure only affects itself.
3. **Systemic/concentration risk from industry-wide dependency on a small number of providers is a real, assessable risk category** — for any organization relying on a similarly central third party, understanding that dependency and its contingency options before an incident matters more than after one.
4. **Downstream organizations should map and test dependency on critical infrastructure providers before an incident**, not discover the depth of that dependency during one.
5. **This reinforces board risk reporting's core translation principle**: a vendor/infrastructure risk finding involving healthcare or safety-critical dependency should be translated to patient-safety and care-access terms, not just financial exposure — the human consequence here was real and reported, not hypothetical.
