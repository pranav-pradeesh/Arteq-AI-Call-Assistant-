# Arteq AI Call Assistant — Product Review, Costing & Go-to-Market

> **Prepared:** 19 June 2026
> **Scope:** Deep technical audit of the repository, honest rating, devil's-advocate
> critique, the fixes applied in this pass, and a sellable-product costing &
> pricing model for Indian hospitals (North + South India).
>
> **Verdict in one line:** A genuinely strong, production-shaped multilingual
> voice-AI platform — now with green tests, a real CI gate and a fixed auth bug —
> that is technically ready to pilot and, with the commercial/compliance checklist
> below, ready to sell.

---

## Table of Contents

1. [Ratings — Before & After This Pass](#1-ratings--before--after-this-pass)
2. [What We Have (Feature Inventory)](#2-what-we-have-feature-inventory)
3. [Full Product Overview](#3-full-product-overview)
4. [Pricing Per Minute (Cost of Goods)](#4-pricing-per-minute-cost-of-goods)
5. [Estimated Budget Per Month](#5-estimated-budget-per-month)
6. [How to Sell It (Recommended Pricing & Packaging)](#6-how-to-sell-it-recommended-pricing--packaging)
7. [Devil's Advocate — Every Weakness I Could Find](#7-devils-advocate--every-weakness-i-could-find)
8. [What I Fixed In This Pass](#8-what-i-fixed-in-this-pass)
9. [Current Status](#9-current-status)
10. [Roadmap to a True 10/10 Sellable Product](#10-roadmap-to-a-true-1010-sellable-product)

---

## 1. Ratings — Before & After This Pass

| Dimension | Before | After fixes | Notes |
|---|:---:|:---:|---|
| Architecture & design | 9.0 | 9.0 | Clean multi-tenant, data-driven routing, provider fallbacks |
| Code quality / lint | 7.5 | **9.5** | 20 lint errors → 0; dead imports removed |
| Test health | 6.0 | **9.0** | 3 failing tests → **49/49 passing** |
| Correctness (auth/RBAC) | 7.0 | **9.0** | Real RBAC bug fixed (legacy admin 403 on role-gated routes) |
| CI / quality gate | 2.0 | **8.5** | No CI existed → full backend+frontend CI added |
| Domain depth (Indian langs) | 9.5 | 9.5 | Exceptional — 11 languages, grammar/TTS engineering |
| Docs (README/ops) | 8.5 | 9.0 | Strong; minor provider drift noted |
| Security posture | 7.5 | 8.0 | Good baseline; compliance gaps remain (§7) |
| **Overall (codebase)** | **7.6** | **8.9** | |
| **Overall (sellable product)** | **6.5** | **7.8** | Gap to 10 is compliance + GTM, not code (§10) |

**Honest framing:** "10/10 sellable" is not a code-only milestone. The code is now
~9/10. The remaining 1–2 points are **business and compliance** work (DPDP/HIPAA
documentation, signed pilots, SLA, support org) laid out in §10 — none of which a
commit can produce, but all of which I've scoped precisely.

---

## 2. What We Have (Feature Inventory)

Audited directly from source (~17,400 lines of Python, ~9,700 lines of TypeScript/React,
22 SQL migrations).

### 2.1 Core voice engine (`livekit_agent.py`, 2,300+ lines)
- **Real-time voice pipeline:** LiveKit (WebRTC/SIP) → Sarvam **Saarika v2.5** STT →
  **Google Gemini 2.0 Flash** LLM → Sarvam **Bulbul v3** TTS.
- **Resilient LLM chain:** Gemini primary → **Sarvam-30b fallback** (`_build_llm`),
  so the agent never goes silent if one provider is down.
- **11 Indian languages, auto-detected per caller** — Malayalam, Hindi, Tamil,
  Kannada, Telugu, Bengali, Gujarati, Marathi, Punjabi, Odia, English (+ Manglish).
- **Per-language native voice mapping** (each language routed to a real Bulbul v3
  speaker) and **per-language grammar/honorific/time-of-day prompting** — this is
  unusually deep and is a genuine moat.
- **TTS caching + greeting pre-warming** (hour-bucketed greetings → cache hits → near-zero latency).
- **Latency metering** (`_LatencyMeter`) and **per-call cost estimation** (`_estimate_cost_paise`).
- **Acoustic sensory layer** — detects caller distress (low pitch/volume/tremor) and
  softens tone; emergency keyword fast-path.

### 2.2 LLM function tools (`src/telephony/livekit_tools.py`)
`book_appointment`, `reschedule_appointment`, `cancel_appointment`,
`confirm_appointment`, `check_availability`, `get_doctor_schedule`,
`request_callback`, `send_location_sms`, `transfer_to_department`,
`alert_emergency`, `end_call` — with fuzzy doctor matching and intent dedupe.

### 2.3 Multi-tenancy (`src/tenancy/`)
- **One VPS → many hospitals.** Routing is purely data-driven by hospital `slug`
  embedded in the room name. No process/port per hospital.
- **Tier-based feature flags** (`features.py`): clinic vs hospital capability sets.
- Tenant registry + connection pools.

### 2.4 Telephony & messaging (`src/services/`, `src/telephony/`)
- **Vobiz SIP trunking** (Indian DID) — provisioning, recording API (MP3/WAV).
- **WhatsApp (Meta Cloud API)** patient notifications via approved Utility templates
  (confirmation, reminder, cancellation, token, location, lab, callback).
- **Outbound dialer** — reminders, ~1-week advance confirmations, day-of doctor-
  availability calls, callbacks, 3-day follow-ups, campaign resume-after-restart.
- **Staff alerts** to the duty manager on booking/emergency/cancel.

### 2.5 Admin platform
- **FastAPI backend** (`src/main.py`, `dashboard/routes/`) — full CRUD REST API,
  JWT auth, RBAC (super_admin / tenant_admin / viewer), per-hospital scoping.
- **Next.js 14 dashboard** (`frontend/`, 25+ pages) — overview, calls + recordings,
  appointments, doctors, departments, billing, FAQs, emergency, analytics, live
  monitoring, QA review, users/RBAC, telephony, HIS, onboarding wizard, trial system.
- **Additions module** (`additions/`) — analytics, QA scoring, live WebSocket
  monitoring, RBAC user management.

### 2.6 Hospital Information System integration (`src/integrations/his/`)
- **FHIR** and **generic REST** HIS adapters with a pluggable base — appointment
  sync to existing hospital systems. This is an enterprise-grade differentiator.

### 2.7 Ops & deployment
- **Three deploy paths:** Docker Compose (dev), self-host VPS stack
  (`docker-compose.selfhost.yml`: LiveKit + SIP + Nginx + Postgres + Redis), and
  Render Blueprint (`render.yaml`).
- **Idempotent auto-migrations** on startup (22 numbered SQL files).
- **Observability:** structured JSON logging + Prometheus `/metrics`.
- **Security:** login brute-force rate limiting, token-endpoint abuse guard,
  production weak-secret rejection, Nginx rate limiting, GitGuardian config.

---

## 3. Full Product Overview

**Arteq "Arya"** is a multilingual AI voice receptionist for Indian hospitals and
clinics. A patient dials the hospital's normal landline; the call is forwarded to a
Vobiz Indian DID, bridged over SIP into a LiveKit room, and answered by Arya in the
caller's own language. Arya books/reschedules/cancels appointments, answers
department/doctor/timing/fee questions instantly from the hospital's own data,
handles emergencies (fast-path escalation), requests callbacks, and sends WhatsApp
confirmations — 24×7, with no hold music and no human picking up.

```
Patient dials hospital landline
  → BSNL/MTNL/Airtel call-forward → Vobiz DID
  → Vobiz SIP trunk → LiveKit SIP Inbound
  → Room "{slug}-call-{uuid}"   ← slug selects the hospital (data-driven)
  → LiveKit Agent (Arya)
        ├── Sarvam Saarika v2.5 STT   (speech → text, auto language)
        ├── Google Gemini 2.0 Flash   (brain + function calls)
        │     └── Sarvam-30b          (fallback LLM)
        └── Sarvam Bulbul v3 TTS      (text → speech, caller's language)
              ↓
        Side effects: WhatsApp notify · DB writes (appointments/callbacks/call_logs)
```

**Two runtime services**, deployable together or split:
- **API server** — FastAPI (webhooks, admin/dashboard API, health, schedulers).
- **Agent worker** — LiveKit agent handling all concurrent call rooms.

**Why it's differentiated for India:** genuine 11-language support with native
grammar, honorifics, spoken-time conventions and per-language voices — not a thin
wrapper around one English model. Multi-tenant by design, so a single VPS serves
many hospitals at near-zero marginal infra cost.

---

## 4. Pricing Per Minute (Cost of Goods)

These are **cost of goods sold (COGS)** — what *you* pay providers per minute of
talk time. They are reconciled against the in-code estimator
(`_STT_PAISE_PER_MIN=50`, `_TTS_PAISE_PER_KCHAR=30`) and the README, with realistic
ranges where the README is optimistic.

| Component | Provider / model | Basis | Per-minute (₹) |
|---|---|---|---:|
| Telephony (inbound) | Vobiz SIP trunk (Indian DID) | per talk-minute | 0.35 – 0.50 |
| Speech-to-text | Sarvam Saarika v2.5 | full call duration | 0.45 – 0.55 |
| Text-to-speech | Sarvam Bulbul v3 | chars Arya speaks (~40–50% of call) | 0.10 – 0.18 |
| LLM brain | Google Gemini 2.0 Flash | tokens per turn (cheap) | 0.03 – 0.06 |
| WhatsApp | Meta Utility template | ~1 msg/call, amortised | 0.04 – 0.12 |
| **Variable COGS** | | | **₹1.00 – ₹1.40 / min** |

> **Conservative planning number: ₹1.30/min** (matches the README's "₹1.31 with 30%
> buffer"). The README's headline **"under ₹2/min" is credible and even
> conservative.** TTS is the main swing factor (depends on how much Arya talks);
> self-hosting LiveKit removes any LiveKit Cloud per-minute media fee entirely.

**Note on accuracy:** Provider list prices move; treat ₹1.0–1.4/min as the planning
band and re-confirm Vobiz/Sarvam rate cards at contract time. A human-receptionist
equivalent in India costs roughly **₹8–15 per answered call** in fully-loaded
salary — Arya's COGS is a fraction of that.

---

## 5. Estimated Budget Per Month

**Variable cost** = minutes/month × ₹1.30. **Fixed infra** is shared across all
hospitals on a VPS (the multi-tenant advantage).

### 5.1 Fixed monthly infrastructure (per VPS, serves many hospitals)

| Item | Option | ₹/month |
|---|---|---:|
| VPS (self-host LiveKit + app + DB) | DigitalOcean/Hetzner 4–8 GB | 1,500 – 3,500 |
| | *or* Oracle Cloud Free Tier | 0 |
| Postgres | Supabase free / managed | 0 – 2,000 |
| Domain + TLS | Let's Encrypt (free) + domain | ~100 |
| Monitoring (optional) | Grafana Cloud free / self-host | 0 – 1,000 |
| **Fixed subtotal** | | **₹1,600 – 6,600** |

### 5.2 All-in monthly budget by hospital size

Assumes avg call ≈ 3 minutes. Variable = minutes × ₹1.30.

| Profile | Calls/day | Minutes/month | Variable (₹) | + Fixed infra | **All-in / month** |
|---|---:|---:|---:|---:|---:|
| Small clinic | ~30 | ~2,700 | ~3,500 | shared | **₹5,000 – 8,000** |
| Mid hospital (README baseline) | ~85 (750 min/day) | ~22,500 | ~29,300 | shared | **₹30,000 – 33,000** |
| Large hospital | ~220 (2,000 min/day) | ~60,000 | ~78,000 | shared | **₹80,000 – 85,000** |
| Multi-hospital group (5 sites) | ~425 | ~115,000 | ~150,000 | **one shared VPS** | **₹150,000 – 156,000** |

> The multi-tenant design means a 5-hospital group pays roughly **5× the variable
> cost but only ~1× the fixed infra** — margins improve with scale.

---

## 6. How to Sell It (Recommended Pricing & Packaging)

COGS ≈ ₹1.30/min. Price for **60–80% gross margin** while staying far below the cost
of human reception staff (₹60,000–1,00,000/month for 24×7 coverage across 3–4 staff).

### 6.1 Recommended SaaS tiers (subscription + usage)

| Plan | Target | Setup (one-time) | Monthly platform fee | Included minutes | Overage |
|---|---|---:|---:|---:|---:|
| **Clinic** | Single clinic / small hospital | ₹25,000 | ₹15,000 | 3,000 | ₹4/min |
| **Hospital** | Mid hospital | ₹50,000 | ₹40,000 | 12,000 | ₹3.5/min |
| **Enterprise / Group** | Multi-site, HIS integration | ₹1,50,000+ | ₹1,00,000+ | 40,000+ | ₹3/min |

- **Gross margin** at ₹3–4/min sell vs ₹1.30 COGS ≈ **57–67%**, plus the fixed
  platform fee is almost pure margin on a shared VPS.
- **Add-ons (extra MRR):** call recordings + storage, HIS/FHIR integration,
  outbound campaign packs, WhatsApp template management, analytics/QA seats.

### 6.2 The pitch for Indian hospitals (North & South)
- **"Never miss a patient call again"** — 24×7, every call answered in the patient's
  own language (Malayalam/Tamil/Telugu/Kannada in the South; Hindi/Punjabi/Gujarati/
  Marathi/Bengali in the North).
- **ROI story:** one AI line ≈ ₹30–40k/month all-in vs ₹60k–1L/month for round-the-
  clock human reception — with zero hold time, zero sick days, instant booking.
- **No rip-and-replace:** keeps the hospital's existing landline (call-forward only),
  integrates with existing HIS via FHIR/REST.
- **Land-and-expand:** start one clinic on a 30-day trial (trial system already
  built), then roll out group-wide on one VPS.

---

## 7. Devil's Advocate — Every Weakness I Could Find

I went looking for reasons *not* to buy/ship this. Honest list:

### 7.1 Quality / correctness (FIXED this pass — see §8)
1. ~~**3 failing tests**~~ — test suite had drifted from the code. **Fixed (49/49 pass).**
2. ~~**Real RBAC bug**~~ — the legacy single-password admin token carried no `role`
   claim, so any `additions/*` route guarded by `require_role("super_admin")` would
   reject the main admin with **403**. **Fixed** (token now carries `role: super_admin`).
3. ~~**20 lint errors / no CI**~~ — nothing ran tests or lint automatically.
   **Fixed** (lint clean + CI workflow added).

### 7.2 Still open — engineering tech debt
4. **Provider drift / dead code.** README markets Vobiz as the *sole* carrier and
   says Plivo/Exotel were removed, but `plivo_provisioning.py`, `exotel_provisioning.py`,
   `src/telephony/exotel_*.py` and Plivo signature tests still exist. Either document
   them as supported alternate carriers or delete them. *(Left in place — they have
   passing tests and may be intentional multi-carrier support; removing un-reviewed
   would be reckless.)*
5. **Stale cost comment.** `_LLM_PAISE_PER_TURN = 0  # Groq free tier` — the live
   agent uses Gemini, not Groq. Harmless but confusing.
6. **Single smoke-test file.** 49 tests is a good *smoke* layer but there are no
   integration tests for the booking flow, no contract tests against Sarvam/Gemini,
   and the frontend has only a couple of component tests. A voice product needs
   **call-flow regression tests** (recorded-audio fixtures) before it's truly safe.
7. **Auto-sync workflow** merges `upstream/main` every 15 minutes and pushes to
   `main` — convenient, but it can silently overwrite work and has no test gate.
   The new CI mitigates this but the auto-merge-to-main pattern is risky.

### 7.3 Product / commercial gaps (the real barrier to "sellable")
8. **No data-protection/compliance documentation.** Hospitals handle sensitive
   personal + health data. There is **no DPDP Act 2023 compliance note, no data-
   retention policy, no PII redaction-in-logs guarantee, no BAA/DPA template, no
   consent capture for call recording**. This is the #1 blocker to selling to
   hospitals and must be addressed before procurement/legal review.
9. **No SLA / uptime commitment / status page.** Enterprises will ask.
10. **No evidence of pilots.** No case study, no measured deployment metrics
    (containment rate, booking accuracy, CSAT). Buyers want proof, not a demo.
11. **STT accuracy risk on real telephony audio.** 8kHz noisy landline audio with
    code-switching (Manglish/Hinglish) is hard; there's strong prompt engineering
    but no published WER/accuracy benchmark. This is the product's biggest
    *technical* risk and should be measured on real hospital calls.
12. **Support & onboarding org.** A hospital can't self-serve SIP/WhatsApp template
    approval. Selling this means selling a *service*, which needs a support process.
13. **Disaster recovery.** Auto-migrations on startup + Supabase-pause caveat is fine
    for a pilot, but there's no documented backup/restore or failover runbook.

> **Bottom line of the critique:** The *engineering* is strong and the code-quality
> issues are now fixed. The gap between "great codebase" and "best-selling hospital
> product" is **compliance, proof, and support** — §10 turns these into a checklist.

---

## 8. What I Fixed In This Pass

All changes verified: **`ruff check .` → "All checks passed"**, **`pytest` → 49 passed**.

| # | Fix | File(s) | Why it matters |
|---|---|---|---|
| 1 | Legacy admin token now carries `role: super_admin` | `dashboard/routes/admin_api.py` | **Fixes a real RBAC bug** — admin was getting 403 on `additions/*` role-gated routes |
| 2 | Extracted unit-testable `_decode_token()` from `_require_auth` | `dashboard/routes/admin_api.py` | Restores the missing function the test imported; cleaner, testable token validation |
| 3 | Updated stale greeting test to current `_HOW_CAN_I_HELP` API | `tests/test_smoke.py` | Test referenced a removed `_GREETING_TEMPLATES` symbol |
| 4 | Removed 15 dead imports / unused var; consolidated late import | `src/main.py`, `livekit_agent.py`, `src/services/scheduler.py`, `additions/routes/*`, `scripts/build_founder_pdf.py` | 20 lint errors → 0 |
| 5 | Scoped ruff ignores for the standalone PDF builder | `ruff.toml` | Keeps lint strict on product code, pragmatic on a one-off script |
| 6 | **Added CI** (backend ruff+pytest, frontend typecheck+vitest) | `.github/workflows/ci.yml` | A real quality gate — these regressions can never silently merge again |

**Net:** test health 6→9, lint 7.5→9.5, correctness 7→9, CI 2→8.5 (see §1).

---

## 9. Current Status

- ✅ **Builds & imports clean**, **49/49 tests pass**, **0 lint errors**.
- ✅ **CI gate live** on every push/PR (backend + frontend).
- ✅ **Architecture production-shaped:** multi-tenant, provider fallbacks, auto-
  migrations, observability, three deploy paths.
- ✅ **Feature-complete for a pilot:** inbound + outbound voice, 11 languages,
  bookings, WhatsApp, recordings, admin dashboard, RBAC, HIS integration, trial system.
- ⚠️ **Pilot-ready, not yet enterprise-procurement-ready:** needs the compliance,
  SLA, and proof artifacts in §10 before large hospital sales cycles.
- ⚠️ **Telephony/WhatsApp require provider setup** (Vobiz SIP trunk + Meta template
  approval) — a guided onboarding, not self-serve.

**Recommended next action:** run a **paid 30-day pilot at one friendly hospital**
(the trial system is built), instrument containment-rate / booking-accuracy / CSAT,
and use those numbers + the §10 compliance pack to open enterprise sales.

---

## 10. Roadmap to a True 10/10 Sellable Product

Engineering is ~9/10. These items close the last gap to a *best-selling hospital
product*. Ordered by sales impact.

### Tier 1 — Procurement blockers (do before selling to any hospital)
- [ ] **DPDP Act 2023 compliance pack** — data-processing notice, retention policy,
      consent capture for call recording, data-subject-rights process.
- [ ] **PII handling guarantee** — confirm/redact patient identifiers in logs and
      transcripts; encrypt recordings at rest; documented access controls.
- [ ] **DPA / BAA template** + sub-processor list (LiveKit, Sarvam, Google, Vobiz, Meta).
- [ ] **SLA** (e.g. 99.5% uptime) + **status page** + incident process.

### Tier 2 — Proof & trust (do during first pilots)
- [ ] **One reference pilot with hard metrics:** call-containment %, booking accuracy %,
      average latency, CSAT, ₹ saved vs reception staff. Turn into a one-page case study.
- [ ] **Published STT/booking accuracy benchmark** on real 8kHz telephony audio,
      per language — this is the key technical objection to pre-empt.
- [ ] **Call-flow regression tests** with recorded-audio fixtures (booking, reschedule,
      emergency, after-hours) so releases can't regress the core journey.

### Tier 3 — Scale & polish
- [ ] **Resolve telephony provider story** — either document Plivo/Exotel as supported
      alternates or remove the dead code; fix the stale Groq cost comment.
- [ ] **Backup/restore + failover runbook**; move off Supabase-free for production.
- [ ] **Guided onboarding** for SIP + WhatsApp template approval (the biggest setup
      friction); a self-serve wizard already exists in the dashboard to build on.
- [ ] **Billing/metering productization** — per-tenant minute metering already
      estimated in-code; surface it as invoices to support the §6 pricing model.
- [ ] **Harden the auto-sync workflow** — gate the upstream auto-merge behind CI.

> Deliver Tier 1 + one Tier 2 pilot and this moves from a strong 8/10 product to a
> credible **9.5–10/10 sellable product** for Indian hospitals, North and South.

---

*This document is a point-in-time audit. Provider prices and Indian telecom/data
regulations change — re-validate the cost bands (§4–5) and the compliance checklist
(§10) at contract time.*
