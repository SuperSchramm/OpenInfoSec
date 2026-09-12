---
name: compliance-gap-risk-assessment
description: Distinguish a compliance gap from an operational risk, and determine whether closing the paperwork gap actually closes the underlying exposure
when_to_use: An audit finding, control assessment, or framework mapping surfaces a gap and GRC needs to determine what it actually means and who needs to act on it
category: security
---

# Compliance Gap vs. Operational Risk Assessment

A finding that a control isn't documented, isn't tested, or doesn't map cleanly to a framework requirement is a compliance gap. Whether that gap corresponds to a real, exploitable exposure is a separate question — and GRC's job is answering both, not just the first one. Closing the paperwork without checking the operational reality produces a clean audit and an unchanged risk posture; flagging the operational reality without tracking the compliance gap produces unaddressed audit exposure. Neither alone is the job.

## Inputs to gather first

Before assessing a finding, confirm or ask for:

1. **The exact framework language the finding cites** — which control, which requirement, verbatim, not a paraphrase
2. **What evidence exists today** — documentation, test results, logs, attestations — versus what's missing
3. **Whether the underlying capability actually exists**, even if undocumented or untested (an unwritten process still running is a different problem than a process that doesn't exist)
4. **Who owns the system or process the finding touches** — GRC assesses, it doesn't remediate alone
5. **Any related findings from other frameworks or prior audits** — a gap rarely exists in isolation; check `regulated_industry_control_overlap.md`-style cross-framework mapping before treating this as a single-framework issue

## The assessment sequence

1. **Classify the finding first: documentation gap, testing gap, or capability gap.** A documentation gap (the control exists and works, but isn't written down) is materially lower-risk than a capability gap (the control doesn't actually exist). Treating them identically either overstates a paperwork problem into a crisis or understates a real exposure as a formality.
2. **Test whether the control actually functions, independent of its documentation status.** Don't infer operational reality from paperwork status in either direction — a well-documented control can still fail in practice, and an undocumented one can still be working.
3. **Determine the residual risk if the gap is left exactly as-is.** What's the realistic exploit path or compliance consequence if this specific gap isn't closed this quarter? A vague "this could be bad" isn't sufficient — state what actually happens, to what asset or data, under what circumstance.
4. **Check whether closing the compliance gap and closing the operational risk require the same fix.** Often they do (writing down and testing a control that already works closes both). Sometimes they don't (documenting a control that doesn't actually function closes the audit finding but leaves the exposure open) — flag this divergence explicitly rather than letting a single remediation ticket imply both are handled.
5. **Route the finding to the right owner with the right urgency.** A capability gap with real exploit potential goes to CyberOps/CISO with operational urgency, not just into the audit-remediation backlog on its own timeline.

## The load-bearing distinction

Passing an audit is the floor, not the ceiling. A control that satisfies a framework's literal requirement can still fail to mitigate the risk that requirement exists to address — frameworks are a proxy for risk reduction, not a guarantee of it. When a finding closes cleanly on paper but you can't independently verify the underlying capability actually works, say so. An audit-clean finding with an unverified operational reality is not the same as a resolved finding.

## Common failure modes

- **Closing findings by documentation alone.** Writing a policy that describes a control satisfies an auditor; it doesn't satisfy the risk unless the control is also tested and confirmed operational.
- **Treating every finding as equally urgent because it's "in the audit."** A documentation gap on a low-criticality system and a capability gap on a system holding regulated data are not the same priority, even if they appear as adjacent line items in the same report.
- **Assessing a finding against one framework in isolation.** The same underlying gap often maps to multiple frameworks' language; missing that means the same root cause gets "found" and separately remediated multiple times across audit cycles.
- **Letting remediation ownership default to GRC.** GRC identifies and tracks the gap; the system or process owner (often CyberOps, sometimes outside security entirely) actually closes it. A finding that sits in GRC's queue without an assigned owner elsewhere isn't actually being remediated.
