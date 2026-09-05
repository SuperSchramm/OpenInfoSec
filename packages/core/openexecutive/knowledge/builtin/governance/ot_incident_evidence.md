# The OT Incident Record: What Actually Failed, Sector by Sector

Risk arguments land differently when they're anchored to a documented incident instead of a hypothetical. The pattern across the most consequential OT-adjacent breaches of the last decade is remarkably consistent: in every case, the entry point or propagation path was a boundary with no formal governance — an ungoverned remote-access interface, or a flat network with no enforced IT/OT segmentation. This is the evidence base for prioritizing boundary governance over point-in-time control fixes.

## Incident Table

| Incident | Sector | Entry Vector | Architectural Failure | Documented Impact |
|---|---|---|---|---|
| Colonial Pipeline (May 2021) | Energy / Pipeline | Compromised VPN credentials on a legacy portal | No MFA; no formal cloud-boundary governance for remote access | Largest U.S. fuel pipeline shut down; $4.4M ransom; fuel shortages across the Eastern Seaboard |
| NotPetya (June 2017) | Cross-sector: shipping, pharma, logistics | EternalBlue/SMBv1 propagation across a flat IT/OT network | No IT/OT DMZ; flat network spanning enterprise and OT | Maersk: $250-300M and a 10-day rebuild; Merck: $870M; FedEx/TNT: $300-400M; ~$10B in global impact |
| WannaCry / NHS (May 2017) | Healthcare | EternalBlue self-propagation across an unsegmented internal network | No enforced zone boundaries between clinical network levels | 80 of 236 NHS trusts affected; 19,494 appointments cancelled; £5.9M documented loss |
| Oldsmar Water (February 2021) | Water / Wastewater | TeamViewer remote access with shared credentials | No governance for remote access to SCADA; no MFA | Attacker briefly raised sodium hydroxide to over 100x safe concentration; caught manually by an operator |
| Change Healthcare (February 2024) | Healthcare | Citrix portal, stolen credentials, no MFA | Cloud portal with no zone governance; nine days of undetected lateral movement | 192.7M individuals affected; $2.457B documented cost; 94% of U.S. hospitals financially impacted |
| Ardent Health (November 2023) | Healthcare | Ransomware propagation across an under-segmented clinical network | Flat or minimally segmented network across 30 hospitals in 6 states | 30 hospitals affected; ERs on divert; Epic offline; ~$80M estimated impact |
| Jaguar Land Rover (September 2025) | Automotive / Manufacturing | IT network intrusion with lateral movement into manufacturing execution systems | Insufficient IT/OT segmentation; production systems reachable from compromised enterprise IT | Five-week global production shutdown across three UK plants; wholesale sales down 43.3% in Q3; ~£1.6-2.1B estimated total UK economic impact — the most financially damaging cyberattack in British history to date |

## The Pattern, Named Plainly

Colonial Pipeline's VPN portal, Change Healthcare's Citrix interface, and Oldsmar's TeamViewer installation are the same architectural failure wearing three different vendor names: an ungoverned cloud-boundary connection with no MFA. NotPetya, WannaCry, Ardent Health, and Jaguar Land Rover are the other half of the same failure: no enforced boundary between IT and OT, so a compromise that should have stayed contained in one zone propagated across the whole environment. Every incident in this table reduces to one of those two root causes, or both at once.

**Dragos's natural-experiment finding (2025 OT Cybersecurity Year in Review)** is the strongest available evidence that segmentation is a causal control, not a compliance checkbox: organizations that enforced strict IT/OT segmentation had significantly shorter recovery times and avoided paying ransom, while organizations without it faced longer recovery, heavier incident response burdens, and greater data-exfiltration exposure. Roughly 70% of OT-related incidents originated in the IT environment — confirming the IT/OT boundary as the single highest-leverage control point across sectors.

## Two Peer-Reviewed Findings That Move This From "Business Risk" to "Safety Risk" in Healthcare

- Neprash et al. (JAMA Health Forum, 2022) documented a 33% relative increase in in-hospital Medicare mortality at hospitals hit by ransomware — a peer-reviewed causal chain from network architecture failure to patient outcome.
- Dameff et al. (JAMA Network Open, 2023) documented degraded stroke-care metrics at *neighboring, unaffected* hospitals during a month-long ransomware attack — the patient-safety impact of an architectural failure spreads beyond the organization that was actually breached.

## Using This Table With a Client

- Lead with the incident that matches the client's sector and the vulnerability you're flagging. A CISO who's heard "you should have MFA" a hundred times will engage differently with "this is the Colonial Pipeline failure mode, in your environment."
- Don't overstate causation on the financial figures — cite them as the disclosed or estimated cost from the source event, not as a universal multiplier for every unpatched vulnerability.
- Use the "flat network" incidents (NotPetya, WannaCry, JLR, Ardent) to justify segmentation investment, and the "ungoverned remote access" incidents (Colonial, Change Healthcare, Oldsmar) to justify MFA and PAM investment — they're different root causes and different remediation budgets, and conflating them weakens both asks.
