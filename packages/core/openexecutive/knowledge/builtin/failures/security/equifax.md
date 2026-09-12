---
domain: security
topic: patch_management_governance_failure
company: Equifax
year: 2017
failure_type: [unpatched_vulnerability, governance_failure, delayed_disclosure, compliance_process_breakdown]
---

# Equifax Data Breach (2017)

## Situation

Equifax was one of three major US credit bureaus, holding highly sensitive financial and personal data — Social Security numbers, birth dates, addresses, and credit histories — on the large majority of American adults, as well as significant numbers of UK and Canadian consumers. As a credit bureau, Equifax's core business model depended on consumers trusting the company with data they had no direct choice in providing, since credit bureaus collect data through financial institutions rather than through direct consumer relationships.

## What Happened

In March 2017, a critical remote code execution vulnerability (CVE-2017-5638) was publicly disclosed in Apache Struts, a web application framework Equifax used in a consumer-facing dispute-portal application. A patch was released the same day the vulnerability was disclosed. Equifax did not apply it. Attackers exploited the unpatched vulnerability starting in mid-May 2017 — roughly two months after the patch was available — and maintained access to Equifax's network for approximately 76 days before detection, during which they exfiltrated personal data on approximately 147 million people. Equifax did not publicly disclose the breach until September 2017, roughly six weeks after internally confirming it, a delay that drew significant regulatory and public criticism, compounded by reports that several executives sold company stock during the window between internal discovery and public disclosure.

## Root Cause

Equifax had a documented internal patch management policy requiring vulnerabilities of this severity to be patched within a defined window. The Apache Struts patch was not applied within that window due to a breakdown in the internal process: the vulnerability scan intended to identify the affected system reportedly did not detect it (due to how the scan was configured relative to where the vulnerable component was deployed), and organizational communication about the vulnerability's applicability to this specific system did not reach the team responsible for patching it. The policy existed; the operational capability to execute it reliably across Equifax's actual environment did not.

## Key Decision Failures

- **A compliant-on-paper patch management policy did not guarantee the underlying capability worked**: Equifax had a documented policy and a defined patching SLA — the failure was in operational execution and visibility, not in the absence of governance structure, which is precisely the compliance-gap-vs-operational-risk distinction: the paperwork existed, the operational reality didn't match it
- **Vulnerability scanning coverage had a gap that wasn't caught until after exploitation**: The scanning process meant to catch exactly this kind of exposure failed to flag the vulnerable system, and that gap in the detection capability itself went unverified until the breach revealed it
- **Organizational communication about vulnerability applicability broke down between teams**: Knowing a vulnerability exists industry-wide is different from confirming and communicating that a specific internal system is affected — that translation step failed
- **Disclosure timeline drew scrutiny independent of the technical failure**: The gap between internal discovery and public disclosure, and stock sales by executives during that window, became a significant part of the regulatory and reputational consequence — separate from, and in addition to, the underlying technical failure

## Lessons

1. **This is the direct case for compliance-gap-vs-operational-risk assessment**: a documented, compliant patch management policy is not evidence the underlying capability actually works — the operational reality (did the scan catch it, did the patch get applied, did the responsible team know) has to be independently verified, not inferred from the policy's existence.
2. **A single missed patch on a public-facing system, given enough dwell time, can produce consequences disproportionate to the apparent size of the initial gap.** 76 days of undetected access from one unpatched component is the argument for both fast patching and strong detection as independent, non-substitutable controls.
3. **Vulnerability scanning coverage itself needs periodic verification.** A scanning program that should have caught something and didn't is a control failure in its own right, distinct from the patching failure it was supposed to prevent from mattering.
4. **Disclosure timeline and internal conduct during the response window are assessed separately from the technical root cause**, and can become the larger part of the consequence — this is a governance and legal dimension GRC and CISO functions both need to own jointly during any incident, not an afterthought to the technical remediation.
5. **Cross-team communication about vulnerability applicability is itself a control that needs to be designed and tested**, not assumed to happen naturally once a CVE is publicly known — this is the specific mechanism that failed here, more than the patching policy itself.
