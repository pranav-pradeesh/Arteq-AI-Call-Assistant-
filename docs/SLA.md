# Service Level Agreement (SLA) — Template

> **Service:** Arteq AI Call Assistant ("Arya"). **Parties:** Arteq and `<Hospital Name>`.
> Bracketed values are negotiated per contract; the defaults below are the recommended
> standard offering. Template for legal/commercial review.

---

## 1. Service commitment (uptime)

| Tier | Monthly uptime target | Eligible for | 
|---|---|---|
| **Standard** | **99.5%** | Clinic / Hospital plans |
| **Enterprise** | **99.9%** | Enterprise / multi-site plans |

"Uptime" = the percentage of the month the inbound call-answer service is able to
accept and handle calls, excluding Scheduled Maintenance and Exclusions (§5).

**Allowed downtime per month:** 99.5% ≈ 3h 39m; 99.9% ≈ 43m.

## 2. Support response times

| Severity | Definition | First response | Target resolution |
|---|---|---|---|
| **S1 — Critical** | Calls not answered service-wide; emergency routing down | **30 min** (24×7) | 4 hours |
| **S2 — Major** | Degraded (high latency, one language/feature failing) | 2 business hours | 1 business day |
| **S3 — Minor** | Dashboard issue, cosmetic, single non-critical feature | 1 business day | 5 business days |
| **S4 — Request** | Config change, new hospital onboarding, question | 2 business days | scheduled |

Emergency-routing failures are always treated as **S1**, given patient-safety impact.

## 3. Service credits

If monthly uptime falls below target, the hospital may request service credits:

| Monthly uptime (Standard 99.5%) | Credit (% of monthly platform fee) |
|---|---|
| 99.0% – < 99.5% | 10% |
| 95.0% – < 99.0% | 25% |
| < 95.0% | 50% |

Credits are the **exclusive remedy** for missed uptime and are requested within 30 days
of the affected month.

## 4. Maintenance

- **Scheduled maintenance:** notified ≥ 48h in advance, performed in a low-traffic
  window (default 01:00–04:00 IST). Excluded from uptime calculation.
- **Emergency maintenance** (security patches): performed as needed with best-effort
  notice.

## 5. Exclusions

Downtime caused by the following is excluded from uptime calculations:
- Force majeure; nationwide telecom/internet outages.
- Failure of a hospital-controlled dependency (e.g. the hospital's own landline/
  call-forwarding, on-prem network).
- Third-party provider outages outside Arteq's control where Arteq has a working
  fallback engaged (the LLM chain already fails over Gemini → Sarvam).
- The hospital's misconfiguration or use outside the documented setup.

## 6. Monitoring, reporting & status page

- **Health endpoint:** `/api/v1/health` reports component readiness (LiveKit, Sarvam,
  Gemini, WhatsApp, Vobiz, DB).
- **Metrics:** Prometheus `/metrics` (latency, call counts, error rates).
- **Status page:** Arteq publishes a public status page reflecting service state and
  incident history. **[Configure]** — recommended providers: a hosted status page or a
  self-hosted page fed by the health endpoint. Operational runbook in
  `INCIDENT_RESPONSE.md`.
- **Monthly SLA report:** uptime %, incident summary, and any credits.

## 7. Business continuity

- **LLM failover:** automatic Gemini → Sarvam fallback so the agent keeps answering if
  the primary LLM is unreachable (`livekit_agent.py::_build_llm`). **[Implemented]**
- **Backups:** database backed up `<daily>` with `<7-day>` retention; restore tested
  quarterly. **[Configure]**
- **Recovery objectives:** RTO `<4h>`, RPO `<24h>` (set per contract).

## 8. Review
SLA reviewed annually or on material service change.

---

> **Disclaimer:** Template only — commercial terms and remedies to be finalised with
> counsel and the customer.
