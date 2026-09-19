# PCI DSS v4.0.1 Scoping and Choosing the Right SAQ

This is an advisory guide for decision support, paraphrased and not a substitute for the PCI SSC documents. The acquirer, card brand or QSA decides the validation type an organization must use. Confirm requirement numbers and eligibility wording against the current PCI DSS v4.0.1 documents and the current SAQ instructions before relying on them. Scope and SAQ decisions should be documented and reviewed by qualified assessors.

## Step 1: Get the scope right first

- **Scope is the cardholder data environment (CDE) plus everything connected to it or able to affect its security.** That includes systems that store, process or transmit account data, systems on the same network segment without segmentation, and systems that provide security services to the CDE, such as authentication, logging, DNS or the administrator workstations that manage the CDE.
- **Scope is a data-flow question.** Trace how account data enters, moves through, and leaves the organization. Requirements 1.2.3 and 1.2.4 call for a network diagram and an account-data-flow diagram. A scope that no one can draw is not a defensible scope.
- **Requirement 12.5.2 requires documented scope confirmation at least every 12 months and after significant changes.** It covers all locations and flows of account data, all system components in scope, and any third parties. Service providers repeat this every six months (12.5.2.1).
- **Segmentation reduces scope only if it is proven.** Network segmentation is not itself required, but if it is used to take systems out of scope, penetration testing must confirm the segmentation works: annually for merchants (11.4.5) and every six months for service providers (11.4.6), and after changes.
- **Connected-to and security-impacting systems are in scope** even when they never touch a card number. A corporate identity provider used to sign in to the payment environment is the usual example.
- **Ways to shrink scope legitimately:** stop storing what is not needed (Requirement 3.2.1), tokenize with a validated provider, use a PCI-listed P2PE solution for card-present payments, and outsource payment capture to a validated provider's hosted page so account data never reaches the merchant's systems.

## Step 2: Identify how card data reaches the organization

The SAQ follows the payment channel and how much the merchant's own systems touch account data.

| Channel or design | What it usually means for validation |
|---|---|
| E-commerce, customer redirected to the processor's hosted payment page | Usually SAQ A |
| E-commerce, processor's hosted payment form embedded in the merchant page through an iframe | Usually SAQ A, subject to the script-protection eligibility criterion below |
| E-commerce, merchant-controlled page or script that collects card data and sends it directly to the processor (direct post, or merchant-hosted JavaScript that builds the card fields itself) | Usually SAQ A-EP, because the merchant's page can affect the security of the transaction |
| E-commerce, merchant servers receive, store or process card data | SAQ D (Merchant) |
| Card-present, validated PCI-listed P2PE solution | SAQ P2PE |
| Card-present, imprint machines or standalone dial-out terminals only | SAQ B |
| Card-present, standalone approved terminals over IP, no other connected systems | SAQ B-IP |
| Virtual terminal on an isolated computer, keyed by hand | SAQ C-VT |
| Payment application connected to the internet or other networks | SAQ C |
| Mobile phone or tablet with a validated SPoC solution | SAQ SPoC |
| Anything else, or a service provider | SAQ D (Merchant) or SAQ D (Service Provider) |

Relative size matters for planning, but exact question counts differ by SAQ version, so do not quote precise numbers without checking the current SAQ documents. SAQ A is the shortest (a few dozen requirements). SAQ A-EP is several times longer (well over a hundred). SAQ D is essentially the full standard (several hundred).

The channel-to-SAQ table above is a starting point. The actual eligibility criteria in each SAQ document decide, and a merchant with more than one channel may need to validate more than one.

## SAQ A and the iframe question

- **SAQ A is for card-not-present merchants (e-commerce, mail order or telephone order) that fully outsource all payment functions** to third-party service providers validated as PCI DSS compliant. The merchant does not electronically store, process or transmit account data on its own systems or premises, and any retained cardholder data is on paper only.
- **An iframe that embeds the processor's payment form generally stays within SAQ A.** The embedded content comes from the processor and the card data goes from the customer's browser straight to the processor. Iframe use alone does not push a merchant to SAQ A-EP.
- **PCI DSS v4.0.1 added an eligibility criterion for that embedded design.** The merchant must confirm that its site is not susceptible to attacks from scripts that could affect the merchant's e-commerce systems. PCI SSC FAQ 1588 says the criterion applies to merchants that embed the processor's hosted payment page or form in an iframe. It does not apply to merchants that redirect the customer to the processor's page or fully outsource the website.
- **There are two ways to meet the criterion.** The merchant can protect against script attacks with techniques such as those in Requirements 6.4.3 and 11.6.1, or it can obtain written confirmation from its PCI-compliant processor or third-party service provider that the solution includes techniques that protect the merchant's payment page from script attacks.
- **6.4.3 and 11.6.1 are not directly assessed for SAQ A.** They were removed from the SAQ A questions and replaced with the eligibility criterion. They still apply to merchants validating with SAQ A-EP or SAQ D, and to payment service providers.
- **Other SAQ A obligations remain.** SAQ A includes data-retention policy, vulnerability management, password and account protections, media protection, external vulnerability scanning by an Approved Scanning Vendor under Requirement 11.3.2, and third-party service provider management under Requirement 12.8. Confirm the exact question list in the current SAQ A.
- **What to ask the processor for:** its current PCI DSS Attestation of Compliance, the written confirmation about script protection on the embedded payment form, and the responsibility matrix showing what the processor covers.

## SAQ A-EP: when the merchant page can affect the transaction

- **SAQ A-EP covers partially outsourced e-commerce.** The merchant's website does not itself receive the card number, but the merchant controls how the customer's browser sends it, so the merchant's site can affect the security of the payment transaction.
- **It has a much larger question set than SAQ A.** It adds secure configuration standards, anti-malware, protection of public-facing web applications, multi-factor authentication, expanded audit logging, and designated 24/7 incident response.
- **Scripts on the payment page are directly in scope,** including Requirements 6.4.3 and 11.6.1.
- **If eligibility is uncertain, the safer answer is the more demanding SAQ,** confirmed in writing with the acquirer, rather than a self-selected SAQ A that an assessor later rejects.

## When a SAQ is not available

- **A QSA-led Report on Compliance (ROC) and Attestation of Compliance (AOC) are typically required** for service providers over the brand thresholds and for the highest merchant levels. Merchant validation levels are set by each payment brand, largely by annual transaction volume, and enforced through the acquirer, so the organization should ask its acquirer which level applies and which validation form is acceptable.
- **A compromise event can raise the validation level** for a merchant regardless of volume.
- **Service providers validate as service providers,** using a SAQ D for Service Providers only if the brands and their acquirers allow self-assessment, and otherwise a ROC.

## Practical guidance for an organization with several payment flows

- **Inventory every place a card can be entered:** patient or customer web portals, call-center phone payments, in-person terminals, mailed forms, invoices and any staff members taking details by email or chat. Email and chat capture of card numbers is a frequent hidden scope expansion.
- **Assign each flow its own validation type,** then confirm the overall approach with the acquirer. It is normal to have a mix, such as SAQ A for a hosted online payment page and SAQ P2PE for clinic terminals.
- **Keep governance requirements in scope even with everything outsourced.** Policies, third-party service provider management (12.8), incident response (12.10), training (12.6) and annual scope confirmation (12.5.2) apply to any entity that has a PCI DSS obligation.
- **Record the decisions.** Write down why each system is in or out of scope and why each SAQ was chosen. Assessors look for that reasoning, and it is the first thing lost in a staff change.
