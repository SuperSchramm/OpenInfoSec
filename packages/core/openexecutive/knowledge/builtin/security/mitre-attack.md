# MITRE ATT&CK: The Tactic Sequence

ATT&CK is a knowledge base of adversary tactics and techniques, organized around the *stages* an attacker moves through during an intrusion. Unlike a compliance framework, ATT&CK describes attacker behavior, not organizational controls — its value is giving CyberOps a common, specific vocabulary for describing what a threat actor actually did (or is attempting to do), which makes threat intelligence, detection engineering, and incident narratives specific and comparable across incidents rather than generic.

## The Tactic Sequence (Enterprise Matrix)

Tactics represent the attacker's *goal* at each stage; techniques (not enumerated here at the summary level) are the specific *methods* used to achieve that goal.

| Stage | Tactic | Attacker's Goal |
|---|---|---|
| 1 | Reconnaissance | Gathering information to plan an attack |
| 2 | Resource Development | Establishing resources (infrastructure, tools, accounts) to support the attack |
| 3 | Initial Access | Gaining an initial foothold in the target environment |
| 4 | Execution | Running malicious code |
| 5 | Persistence | Maintaining access across restarts, credential changes, and other interruptions |
| 6 | Privilege Escalation | Gaining higher-level permissions |
| 7 | Defense Evasion | Avoiding detection |
| 8 | Credential Access | Stealing account names and passwords |
| 9 | Discovery | Understanding the environment being operated in |
| 10 | Lateral Movement | Moving through the environment to other systems |
| 11 | Collection | Gathering data of interest to the attacker's objective |
| 12 | Command and Control | Communicating with compromised systems to direct further action |
| 13 | Exfiltration | Stealing data out of the environment |
| 14 | Impact | Manipulating, interrupting, or destroying systems and data |

The current Enterprise matrix (v18, released October 2025) contains 216 techniques and 475 sub-techniques organized beneath these 14 tactics.

## Why This Matters Operationally

Mapping an incident (or a piece of threat intelligence) to specific tactics/techniques makes the applying-threat-intelligence skill's core argument concrete: "this threat actor has hit similar organizations using this specific TTP" is only checkable and comparable across incidents because ATT&CK provides the shared vocabulary to describe it precisely, rather than in each analyst's own informal language.

## Common Misapplications to Flag

- **Treating the tactic sequence as strictly linear.** Real intrusions loop back — an attacker may re-run Discovery after Lateral Movement, or re-establish Persistence after losing an initial foothold. The matrix describes categories of behavior, not a fixed script every intrusion follows in order.
- **Focusing detection engineering only on Initial Access.** An attacker who evades initial detection still has to execute techniques across many later tactics to achieve their objective — later-stage detection (Lateral Movement, Collection, Exfiltration) is a legitimate and often more reliable detection layer than trying to catch everything at the perimeter.
- **Using ATT&CK to describe an incident without mapping to specific, cited techniques.** "They used lateral movement" is vague; citing the specific technique used is what makes the mapping actually useful for detection engineering or comparison to other incidents.

*Note: ATT&CK is actively maintained and versioned, with techniques added, deprecated, and revised over time — verify current technique-level detail against the live MITRE ATT&CK knowledge base rather than treating any static summary as current.*
