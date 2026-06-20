# Arteq — Compliance, SLA & Proof Documentation

The artifacts a hospital's procurement, legal, and IT-security teams ask for before an
enterprise sales cycle. Together with the codebase, these move Arteq from
*pilot-ready* to *enterprise-procurement-ready*.

> **Honest framing:** these documents define real, defensible policies and processes
> grounded in the actual codebase. They still require (a) **legal counsel sign-off**
> before production use, and (b) the proof artifacts to be **filled with real pilot
> data**. They are deliberately explicit about what is implemented vs. configured vs.
> on the near-term roadmap.

## Compliance (`compliance/`)
| Doc | Purpose |
|---|---|
| [DPDP_COMPLIANCE.md](compliance/DPDP_COMPLIANCE.md) | DPDP Act 2023 pack: roles, lawful basis, notice, security, retention, data-principal rights, localisation, checklist |
| [DATA_PROCESSING_AGREEMENT.md](compliance/DATA_PROCESSING_AGREEMENT.md) | DPA template + **approved sub-processor list** |
| [PII_AND_SECURITY.md](compliance/PII_AND_SECURITY.md) | Data-flow map + security controls (Implemented / Configure / Roadmap) + known-gap register |
| [CALL_RECORDING_CONSENT.md](compliance/CALL_RECORDING_CONSENT.md) | Verbal consent process + multilingual scripts + emergency exception |

## Service levels
| Doc | Purpose |
|---|---|
| [SLA.md](SLA.md) | Uptime targets, support response times, service credits, maintenance, continuity |
| [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) | Incident severities, response flow, **72-hour breach procedure**, status page, post-incident review |

## Proof (`pilot/`)
| Doc | Purpose |
|---|---|
| [ACCURACY_BENCHMARK.md](pilot/ACCURACY_BENCHMARK.md) | Repeatable accuracy methodology + results template (real telephony audio, per language) |
| [CASE_STUDY_TEMPLATE.md](pilot/CASE_STUDY_TEMPLATE.md) | Post-pilot case study with baseline/after metrics + ROI |

## Related
- [`../PRODUCT_REVIEW.md`](../PRODUCT_REVIEW.md) — ratings, costing, pricing, and the roadmap these docs deliver against.
- [`../README.md`](../README.md) — product & deployment documentation.
