# PCI DSS v4.0.1: Version Status and the Twelve Requirements

This is an advisory summary written for executive decision support. It paraphrases the standard and is not a substitute for it. The authoritative text is the PCI DSS document published by the PCI Security Standards Council (PCI SSC) in its Document Library. Assessment decisions belong to the organization's Qualified Security Assessor (QSA), acquirer, or card brand, so specific requirement numbers here should be confirmed against the current standard before anyone relies on them in an audit.

## Which version applies

- **PCI DSS v4.0.1 is the only active version as of this writing (September 2026); confirm the current version in the PCI SSC Document Library.** It was published in June 2024. PCI DSS v4.0 was retired on December 31, 2024. v3.2.1 was retired earlier, on March 31, 2024.
- **v4.0.1 is a limited revision, not a new standard.** It added and removed no requirements. It corrected typographical and formatting errors and clarified the intent of some wording.
- **Every "future-dated" requirement is now mandatory.** v4.0 introduced 64 new requirements. 51 of them were future-dated to March 31, 2025. Any assessment or self-assessment completed after that date is measured against the full standard, with no transition period left.
- **Auditing against an older version is not acceptable practice.** Assessments, gap analyses and self-assessments should reference v4.0.1 requirement numbers. A v3.2.1 or v4.0-era checklist will miss requirements that are now in force, such as those for payment-page scripts, MFA into the whole cardholder data environment, and authenticated internal scans.
- **Two validation approaches exist.** The *defined approach* follows the stated requirement and testing procedure. The *customized approach* lets an entity meet a control's stated objective a different way, with its own documented controls and a QSA's testing. Organizations completing a Self-Assessment Questionnaire (SAQ) generally use the defined approach only.
- **Targeted risk analysis (TRA)** is a documented analysis, defined in Requirement 12.3.1, that lets an entity choose the frequency of specific recurring activities. Several requirements say "at the frequency defined by the entity's targeted risk analysis." The analysis must be documented, reviewed at least every 12 months, and be defensible to an assessor.

## What data the standard protects

- **Account data** is cardholder data plus sensitive authentication data.
- **Cardholder data** is the primary account number (PAN), and, when stored with the PAN, the cardholder name, expiration date and service code.
- **Sensitive authentication data (SAD)** is full magnetic-stripe or chip track data, the card verification code printed on or encoded in the card (CVV2, CVC2, CID, CAV2), and the PIN or PIN block.
- **SAD must not be stored after authorization, even if encrypted** (Requirement 3.3.1). The PAN may be stored only when there is a business need, and then it must be rendered unreadable.
- **The cardholder data environment (CDE)** is the people, processes and technology that store, process or transmit account data, plus systems that are connected to or could affect the security of those systems. Scope follows the data and its connections, not the org chart.

## The twelve principal requirements

1. **Requirement 1, Install and maintain network security controls.** Firewalls and equivalent controls between trusted and untrusted networks and around the CDE. Requires network diagrams and account-data-flow diagrams (1.2.3, 1.2.4), rule review at least every six months, and restrictive inbound and outbound traffic rules.
2. **Requirement 2, Apply secure configurations to all system components.** Documented hardening standards, changing vendor defaults, disabling unneeded services, and encrypting non-console administrative access.
3. **Requirement 3, Protect stored account data.** Minimize and define retention, never keep SAD after authorization, mask displayed PAN (at most the BIN, the first six or eight digits, and the last four), and render stored PAN unreadable using strong cryptography, truncation or tokenization. Includes key management for the keys protecting stored data.
4. **Requirement 4, Protect cardholder data with strong cryptography during transmission over open, public networks.** Strong cryptography and trusted certificates for PAN in transit, with an inventory of the trusted keys and certificates in use.
5. **Requirement 5, Protect all systems and networks from malicious software.** Anti-malware on systems commonly affected, kept current and running, plus anti-phishing mechanisms for users (5.4.1).
6. **Requirement 6, Develop and maintain secure systems and software.** Vulnerability identification and patching (critical patches generally within one month), an inventory of bespoke and custom software and third-party components (6.3.2), secure development and change control, protection of public-facing web applications (6.4.1, 6.4.2), and control of scripts on payment pages (6.4.3).
7. **Requirement 7, Restrict access to system components and cardholder data by business need to know.** Least privilege, a documented access model, and review of all user accounts and privileges at least every six months (7.2.4).
8. **Requirement 8, Identify users and authenticate access to system components.** Unique IDs, strong authentication (passwords of at least 12 characters where supported), and multi-factor authentication for all access into the CDE, not only remote or administrative access (8.4.2).
9. **Requirement 9, Restrict physical access to cardholder data.** Facility controls, visitor handling, media protection, and protection and inspection of point-of-interaction (payment terminal) devices against tampering and substitution.
10. **Requirement 10, Log and monitor all access to system components and cardholder data.** Audit logs for all system components in scope, time synchronization, protection of log integrity, automated daily log review (10.4.1.1), retention of 12 months with the most recent 3 months immediately available, and detection of failures of critical security controls (10.7.2).
11. **Requirement 11, Test security of systems and networks regularly.** Wireless access point checks, internal and external vulnerability scans at least every three months, penetration testing at least annually and after significant change, segmentation testing, intrusion detection or prevention, change detection, and tamper detection on payment pages (11.6.1).
12. **Requirement 12, Support information security with organizational policies and programs.** A security policy reviewed at least annually, targeted risk analyses, an inventory and annual confirmation of scope (12.5.1, 12.5.2), security awareness training (12.6), third-party service provider management (12.8), and an incident response plan (12.10).

There are also appendices. Appendix A1 covers additional requirements for multi-tenant service providers, A2 covers SSL and early TLS for legacy point-of-sale terminals, and A3 covers Designated Entities Supplemental Validation.

## Requirements that surprise organizations still following older checklists

- **Requirement 6.4.3 and 11.6.1 (payment page scripts).** Every script that loads and runs in the consumer's browser on a payment page must be inventoried, justified and authorized, and its integrity must be assured. A mechanism must also detect unauthorized changes to the payment page and its HTTP headers, evaluated at least weekly or at the frequency set by a targeted risk analysis.
- **Requirement 8.4.2 (MFA everywhere into the CDE).** Multi-factor authentication is needed for every access into the CDE, including from inside the corporate network and for non-administrators.
- **Requirement 8.3.6 (passwords).** At least 12 characters (8 only where the system cannot support 12), with both letters and numbers.
- **Requirements 8.6.1 to 8.6.3 (system and application accounts).** Interactive use of these accounts is restricted, no passwords hard-coded in scripts or code, and password strength and change frequency set by a targeted risk analysis.
- **Requirement 11.3.1.2 (authenticated internal scans).** Internal vulnerability scans of in-scope systems must use authenticated scanning where the systems can support it. The number is 11.3.1.2. Its neighbour 11.3.1.1 is a different requirement (managing lower-ranked vulnerabilities by risk), and 11.3.1 itself is the quarterly internal scan. Cite authenticated scanning as 11.3.1.2.
- **Requirement 12.5.2 (scope confirmation).** Documented confirmation of PCI DSS scope at least every 12 months and after significant change to the environment. Service providers repeat this every six months (12.5.2.1).
- **Requirement 12.10.7 (stored PAN found unexpectedly).** Incident response procedures must exist and start immediately when stored PAN is found anywhere it is not expected. This turns data discovery into an ongoing capability.
- **Requirements 12.1.1 to 12.1.4 (policy and roles).** 12.1.1 requires an overall information security policy that is published and distributed. 12.1.2 requires it to be reviewed at least every 12 months and updated when the environment changes. 12.1.3 requires it to define security roles and responsibilities for all personnel, with acknowledgement from them. 12.1.4 requires that responsibility for information security be formally assigned to a chief information security officer or an equivalent executive. Roles for individual requirements are documented under each requirement's own "roles and responsibilities" sub-requirement (for example 1.1.2 and 6.1.2).
- **Requirements 12.3.1 and 12.3.2 (targeted risk analysis).** 12.3.1 covers the analysis an entity performs to choose the frequency of an activity where a requirement allows it. 12.3.2 covers the analysis required for each requirement the entity meets with the customized approach. They are different obligations; do not cite one for the other.
- **Requirements 12.6.3.1 and 12.6.3.2 (training content).** Awareness training must cover phishing and related social-engineering attacks (12.6.3.1) and the acceptable use of end-user technologies (12.6.3.2).
- **Requirement 5.4.1 (anti-phishing mechanisms), 6.4.2 (automated public-facing web application protection, such as a web application firewall), 10.4.1.1 (automated log review) and 3.4.2 (blocking copy or relocation of PAN in remote-access sessions).** All are now in force.

## Third-party service providers and shared responsibility

- **Outsourcing does not remove the obligation.** An entity that uses a payment processor, hosting provider, or managed service must still manage the relationship (Requirement 12.8): keep a list of third-party service providers and what each does, hold written agreements acknowledging their responsibility for account data security, perform due diligence, and confirm each provider's PCI DSS compliance status at least every 12 months.
- **A responsibility matrix (12.8.5)** records which PCI DSS requirements the provider covers, which the customer covers, and which are shared. It is the most common place for a gap to hide.
- **Service providers must also support their customers** (12.9), including providing the information customers need for their own assessments.
- **Reducing scope through a provider** works only if the provider is validated, the integration keeps account data off the customer's systems, and the customer can document that. Examples are a validated PCI-listed point-to-point encryption (P2PE) solution or hosted payment pages.

## Incident response under PCI DSS

- **Requirement 12.10.1** requires an incident response plan that is ready to be activated. It must define roles and responsibilities, communication and contact strategies (including notifying payment brands and the acquirer), containment and recovery procedures, and coverage of all critical system components.
- **The plan must be tested and reviewed at least every 12 months** (12.10.2), and designated personnel must be available around the clock to respond to alerts (12.10.3) and be appropriately trained (12.10.4).
- **The plan must cover alerts from security monitoring**, including intrusion detection and prevention, network security controls, change-detection mechanisms and payment-page change detection (12.10.5).
- **Notification of the acquirer and card brands is a contractual obligation** for a suspected or confirmed compromise of account data. It sits beside, not instead of, any legal breach-notification duties under state, federal or sector law. The timeline comes from the merchant agreement and brand rules, so counsel and the acquirer relationship owner need to be involved early.
- **A payment brand may require a PCI Forensic Investigator (PFI)** to investigate a suspected compromise. Preserve evidence and route the investigation through counsel where privilege matters.

## Common misapplications to flag

- **Using v3.2.1 or v4.0 mapping tables.** The requirement numbering and content moved. Re-map to v4.0.1.
- **Treating a validated third party as full scope removal.** It reduces scope and shifts responsibility. It does not remove the entity's own obligations for the parts that remain, such as Requirement 12 governance, the payment page, and vendor management.
- **Assuming a cloud or SaaS environment is "out of scope" by default.** Cloud and shared-responsibility hosting changes who operates a control, not whether the control applies to the data.
- **Confusing PCI DSS with HIPAA, SOC 2 or NIST controls.** They overlap heavily, but PCI DSS is contractual with the card brands and acquirers, defines its own scope and validation reporting, and does not accept another framework's report as its own assessment.
