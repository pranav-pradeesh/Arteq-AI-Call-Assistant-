# Data Processing Agreement (DPA) — Template

> **Between:** `<Hospital Name>` ("Data Fiduciary") and `Arteq` ("Data Processor").
> **Purpose:** Govern Arteq's processing of patient personal data when operating the
> Arya voice assistant on the hospital's behalf, in line with the DPDP Act 2023.
> **Status:** Template for legal review. Bracketed `<...>` fields are completed per deal.

---

## 1. Subject matter & duration
Arteq processes patient personal data solely to provide the voice-assistant service
for the term of the service agreement and for the limited wind-down period in §9.

## 2. Nature & purpose of processing
Receiving inbound calls; speech-to-text and text-to-speech; LLM-driven dialogue;
appointment booking/rescheduling/cancellation; emergency routing; outbound reminders/
confirmations; WhatsApp notifications; storage of call logs and (optional) recordings.

## 3. Categories of data & data principals
- **Data principals:** patients and callers of `<Hospital Name>`.
- **Data:** phone number, name, appointment details, call transcript, optional voice
  recording, derived call metadata. May include health-related information.

## 4. Processor obligations (Arteq)
1. Process only on the hospital's **documented instructions**.
2. Apply the **technical & organisational security measures** in `DPDP_COMPLIANCE.md` §5.
3. Ensure personnel are bound by **confidentiality**.
4. Engage **sub-processors only** as listed in §7 and flow down equivalent terms.
5. **Assist** the hospital with data-principal requests and grievance redressal.
6. **Notify** the hospital of a personal-data breach **without undue delay and within
   72 hours** of becoming aware (see `../INCIDENT_RESPONSE.md`).
7. **Delete or return** all personal data on termination (§9).
8. Make available information necessary to demonstrate compliance and allow **audits**.

## 5. Fiduciary obligations (Hospital)
1. Provide lawful **notice and obtain consent** from patients (verbal consent script +
   published privacy notice).
2. Issue lawful, documented **processing instructions**.
3. Designate a **Data Protection contact** for requests and grievances.

## 6. Data principal rights
Arteq will assist the hospital in fulfilling access, correction, erasure, consent-
withdrawal and grievance requests within the SLAs in `DPDP_COMPLIANCE.md` §7.

## 7. Approved sub-processors

| Sub-processor | Function | Data exposed | Processing region | Notes |
|---|---|---|---|---|
| **LiveKit** | Real-time media (WebRTC/SIP), agent orchestration | Audio stream | Cloud region or **self-hosted in India** | Self-host eliminates third-party media handling |
| **Sarvam AI** | Speech-to-text, text-to-speech (+ fallback LLM) | Audio, transcript text | India | Indian-language AI provider |
| **Google (Gemini)** | Primary LLM dialogue | Transcript text (turn context) | Multi-region | Disable & rely on Sarvam-only if strict localisation required |
| **Vobiz** | SIP telephony, call recording storage | Phone number, audio, recordings | India | DID carrier |
| **Meta (WhatsApp Cloud API)** | Patient notifications | Phone number, name, appt details | Multi-region | Only if WhatsApp enabled |
| **Database host** (Supabase / cloud Postgres / self-host) | Persistent storage | All stored personal data | **Choose Indian region** | Encryption at rest required |

> The hospital **approves this list** at signing. Arteq gives prior notice of any
> intended change and the hospital may object. To minimise sub-processors and keep
> data in India, deploy **self-hosted LiveKit + Sarvam-only LLM + Indian-region DB**.

## 8. International transfers
Where a sub-processor processes data outside India, the parties rely on the approved
list (§7) and applicable safeguards. The hospital may require an India-only
configuration; Arteq supports this deployment mode.

## 9. Return & deletion on termination
On termination, Arteq deletes or returns all personal data within **<30> days**,
including recordings held at the telephony provider, and certifies deletion in writing,
save where retention is legally required.

## 10. Breach notification
Arteq notifies the hospital within **72 hours** of awareness with: nature of the breach,
data/individuals affected, likely consequences, and remediation steps. Full process in
`../INCIDENT_RESPONSE.md`.

## 11. Audit
The hospital (or an appointed auditor) may audit Arteq's compliance on reasonable
notice, no more than once per year except after a breach.

## 12. Liability & governing law
As per the master service agreement. Governing law: **India**; jurisdiction: `<city>`.

---

**Signatures**

| | Data Fiduciary (`<Hospital>`) | Data Processor (Arteq) |
|---|---|---|
| Name | | |
| Title | | |
| Date | | |

> **Disclaimer:** Template only — not legal advice. Have counsel review before signing.
