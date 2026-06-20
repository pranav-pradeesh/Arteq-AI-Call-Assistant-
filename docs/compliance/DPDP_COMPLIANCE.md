# DPDP Act 2023 — Compliance Pack

> **Applies to:** Arteq AI Call Assistant ("Arya") deployed for Indian hospitals/clinics.
> **Regulation:** Digital Personal Data Protection Act, 2023 (India).
> **Status:** Controls and policies defined below. Items marked **[Implemented]** are
> enforced in code today; **[Configure]** require deployment-time setup;
> **[Roadmap]** are committed near-term hardening. This document is written to be
> handed to a hospital's procurement/legal team and **should be reviewed by counsel
> before contract signature.**

---

## 1. Roles under the DPDP Act

| Role | Party | Notes |
|---|---|---|
| **Data Principal** | The patient/caller | The individual whose personal data is processed |
| **Data Fiduciary** | The hospital/clinic | Determines purpose & means of processing |
| **Data Processor** | Arteq (the vendor) | Processes patient data **on behalf of** the hospital, under contract |
| **Sub-processors** | LiveKit, Sarvam AI, Google, Vobiz, Meta, DB host | See `DATA_PROCESSING_AGREEMENT.md` |

Arteq operates as a **Data Processor**. The hospital remains the Data Fiduciary and
is the accountable party to the patient. The Data Processing Agreement (DPA) binds
Arteq to process only on the hospital's documented instructions.

---

## 2. Personal data we process

| Data element | Source | Where stored | Sensitivity |
|---|---|---|---|
| Caller phone number | Telephony (Vobiz) | `call_logs.caller` (Postgres) | Personal identifier |
| Patient name | Spoken during call | `appointments`, `call_logs.transcript` | Personal identifier |
| Call transcript | STT output | `call_logs.transcript` (JSON) | May contain **health data** |
| Voice recording | Telephony (optional) | Vobiz media store; URL in `call_logs.recording_url` | Health/biometric-adjacent |
| Appointment details | Booking flow | `appointments` | Health-related |
| Emotional/acoustic state | Acoustic sensory layer | `call_logs.emotional_state` | Derived health-adjacent |

> Health-related data attracts heightened expectations. Treat transcripts and
> recordings as the most sensitive category and apply §5 controls accordingly.

---

## 3. Lawful basis & purpose limitation (DPDP §4–6)

- **Consent** is the primary basis. The patient is informed at the **start of every
  call** (see `CALL_RECORDING_CONSENT.md`) of (a) who is calling/answering, (b) that
  the call may be recorded/transcribed, (c) the purpose, and (d) how to opt out.
- **Purpose:** appointment booking/management, clinical-line routing, emergency
  escalation, and service notifications **only**. Data is **not** used for
  advertising, profiling, or sold to third parties.
- **Purpose limitation is enforced in the agent prompt** **[Implemented]** — Arya is
  scoped to hospital matters only and refuses off-topic requests.

---

## 4. Notice to the Data Principal (DPDP §5)

A plain-language notice must be available in the patient's language. The call-start
consent script (in `CALL_RECORDING_CONSENT.md`) satisfies the verbal notice
requirement; the hospital must also publish a written privacy notice. A template
written notice is provided in §9 below.

---

## 5. Security safeguards (DPDP §8(5))

### Implemented in code today
- **Authentication:** bcrypt password hashing (cost 12) for all dashboard users. **[Implemented]**
- **Authorisation:** RBAC (`super_admin` / `tenant_admin` / `viewer`) with
  **per-tenant data isolation** — a hospital user can only access their own
  hospital's data (`user_tenants` scope checks in `admin_api.py`). **[Implemented]**
- **Tokens:** signed JWT (HS256); production startup **rejects weak/default secrets**
  and short admin passwords (`settings.py::_reject_weak_secrets_in_production`). **[Implemented]**
- **Abuse protection:** login brute-force rate limiting (5/10 min) and a token-endpoint
  abuse guard. **[Implemented]**
- **Secret hygiene:** GitGuardian secret scanning in CI; no secrets in source. **[Implemented]**
- **Transport security:** TLS termination at Nginx with HSTS-capable config in the
  self-host stack. **[Configure]** (provision certs per README)
- **Multi-tenancy:** one logical dataset per hospital; cross-tenant access is denied
  at the API layer. **[Implemented]**

### Configure at deployment
- **Encryption at rest:** use a managed Postgres that encrypts at rest (Supabase and
  major cloud Postgres do by default) **or** enable disk encryption on the VPS. **[Configure]**
- **Recording storage:** call recordings reside with Vobiz; restrict access via the
  Vobiz console and the hospital-scoped dashboard API. Enable recordings only with
  consent. **[Configure]**
- **Network:** open only the firewall ports listed in the README. **[Configure]**

### Roadmap hardening (committed)
- **PII redaction in application logs** — a redaction helper that masks phone numbers
  and names in structured-log events before emission. *(Transcripts are written to the
  DB, not the logs, today; this closes residual leakage in operational logs.)* **[Roadmap]**
- **At-rest field encryption** for `call_logs.transcript` and `caller`. **[Roadmap]**
- **Automated retention purge** job (see §6). **[Roadmap]**

---

## 6. Data retention & erasure

| Data | Default retention | Rationale |
|---|---|---|
| Appointments | Per hospital policy (default 24 months) | Continuity of care |
| Call transcripts | 90 days (configurable) | Quality assurance, dispute resolution |
| Call recordings | 30 days (configurable) | Minimise sensitive-data footprint |
| Call metadata (logs) | 12 months | Analytics, billing reconciliation |
| Dashboard user accounts | Until offboarding | Access control |

- Retention windows are **hospital-configurable** and recorded in the DPA.
- **Erasure on request:** patient erasure requests are fulfilled within **30 days**
  (see §7). A documented purge procedure deletes the patient's `call_logs`,
  `appointments`, and associated recordings.
- **[Roadmap]** A scheduled purge job will enforce retention windows automatically;
  until then, purge is run as a documented operational procedure.

---

## 7. Data Principal rights (DPDP §11–14) — fulfilment process

| Right | How it is fulfilled | SLA |
|---|---|---|
| Access / summary of data | Hospital staff export from dashboard (calls, appointments) | 7 working days |
| Correction | Edit via dashboard (appointments, contact) | 7 working days |
| Erasure | Documented purge procedure (§6) | 30 days |
| Grievance redressal | Hospital's Data Protection contact → escalate to Arteq DPO | Ack 72h |
| Withdraw consent | Opt-out captured on-call; disables recording/notifications | Immediate |

The **hospital is the first point of contact** (Data Fiduciary). Arteq supports the
hospital operationally as Processor.

---

## 8. Cross-border & data localisation

- Default deployment keeps data **in India** (self-host VPS in an Indian region, or an
  Indian-region managed Postgres). **[Configure]**
- Sub-processors that may process data outside India (e.g. LLM inference) are listed
  in the DPA with their regions; the hospital approves the sub-processor list. If
  strict localisation is required, deploy with in-region providers only and disable
  any out-of-region sub-processor.

---

## 9. Patient privacy notice (template — hospital publishes)

> **<Hospital Name> — Voice Assistant Privacy Notice**
> When you call <Hospital Name>, your call may be answered by an AI assistant and may
> be recorded and transcribed to book and manage your appointments and to help in
> emergencies. We process your phone number, name and the details you share for these
> purposes only. We do not sell your data or use it for advertising. Your data is
> stored securely and retained only as long as needed. You may ask us to access,
> correct or delete your data, or decline recording, by contacting
> <hospital data-protection email/phone>. Grievances: <Data Protection Officer contact>.

---

## 10. Compliance checklist (per deployment)

- [ ] Signed DPA in place between hospital and Arteq (`DATA_PROCESSING_AGREEMENT.md`)
- [ ] Sub-processor list reviewed and approved by the hospital
- [ ] Call-start consent script enabled in all caller languages
- [ ] Written privacy notice published by the hospital (§9)
- [ ] Data stored in an Indian region; encryption at rest verified
- [ ] Retention windows configured per hospital policy
- [ ] Erasure/grievance contacts documented and staff trained
- [ ] TLS enabled; firewall restricted to required ports
- [ ] Legal counsel sign-off obtained

> **Disclaimer:** This pack is a compliance-readiness foundation, not legal advice.
> The hospital and Arteq should obtain independent legal review before processing
> patient data in production.
