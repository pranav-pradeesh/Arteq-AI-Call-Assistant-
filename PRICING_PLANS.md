# Arya — AI Voice Receptionist for Hospitals & Clinics
## Pricing Plans & ROI (for founder review)

*Prepared June 2026. All figures in Indian Rupees (₹). Prices exclude 18% GST.*

---

## 1. What Arya does (in one line)

Arya answers your hospital's phone calls in Malayalam (and Hindi, Tamil, Telugu,
Kannada, English) — 24 hours a day, on every line at once. She books
appointments, answers questions about doctors, timings, and departments, sends
confirmation SMS, and transfers to staff when needed. She never sleeps, never
takes a break, and never puts a patient on hold.

---

## 2. Why hospitals lose money today

A normal hospital front desk has 1–3 people answering phones during working
hours only. The result:

- **Missed calls.** At busy times and after hours, 30–40% of calls go
  unanswered. Every missed call is often a lost appointment.
- **Slow service.** Callers wait on hold; some hang up and call a competitor.
- **Staff cost.** Each receptionist costs ₹15,000–₹25,000/month and works only
  8 hours a day, handling one call at a time.

Arya fixes all three at once: she picks up instantly, handles unlimited calls
together, and works round the clock.

---

## 3. How much call traffic does a hospital get? (Kerala)

Based on public Kerala health data and typical call patterns (average call ≈ 2.5–3 minutes):

| Type of facility | Patients/day | Calls/month (approx.) | Talk-time/month |
|---|---|---|---|
| Single-doctor clinic | 30–60 | 600–1,000 | **500–1,000 min** |
| Small clinic / polyclinic | 60–120 | 1,000–2,500 | **1,000–2,500 min** |
| Mid-size hospital (50–150 beds) | 150–400 | 9,000–11,000 | **25,000–30,000 min** |
| Large hospital (150–300 beds) | 400–800 | 15,000–22,000 | **40,000–60,000 min** |
| Multi-specialty / very large | 800+ | 22,000–36,000 | **60,000–100,000 min** |

*Reference: Kerala government medical college hospitals see 4,000–4,500 outpatients
per day; a primary health centre averages ~80–85/day. Private hospitals scale
with bed count and specialties.*

---

## 4. What it costs us to run Arya (our side)

Every minute Arya is on a call, we pay these providers. We run two setups:
**Managed Cloud** (fast to launch, LiveKit's rented cloud) and **Self-Hosted on
one VPS** (LiveKit running on our own ₹799 server — same quality, much lower
cost). We move customers to the VPS setup as volume grows. Full details and
caveats in section 4a.

| Cost component | Managed Cloud | Self-Hosted (VPS) |
|---|---|---|
| Phone line (telephony) | ₹0.60 | ₹0.60 |
| Voice infrastructure (LiveKit) | ₹1.20 | ~₹0.10 |
| Speech understanding (Sarvam) | ₹0.50 | ₹0.50 |
| Speech voice (Sarvam) | ₹0.15 | ₹0.15 |
| AI brain (Groq) | ₹0.20 | ₹0.20 |
| SMS + buffer | ₹0.10 | ₹0.10 |
| **Total cost per minute** | **≈ ₹2.75** | **≈ ₹1.65** |

On the VPS setup the database, hosting, and voice server all live on that same
₹799/month box — there is no separate platform fee to add.

### Telephony option: Plivo vs Exotel

We can run the phone line through either provider — the rest of Arya is
identical:

| Provider | Per-minute call cost | Notes |
|---|---|---|
| **Plivo** (current) | ₹0.60/min, flat | Transparent published pricing, no seat fees, pure pay-as-you-go. Best for our model. |
| **Exotel** | ₹0.50–0.80/min (custom) | India-focused, strong local support and compliance; but adds a monthly platform fee and per-seat charges, and pricing is quote-based. Better if a hospital specifically wants an Indian carrier or already uses Exotel. |

**Recommendation:** stay on Plivo by default (cheaper, predictable). Offer
Exotel only when a customer requests it; if Exotel's negotiated rate rises above
Plivo's ₹0.60/min, the difference is passed through, not absorbed.

---

## 4a. Keeping every call under ₹2/minute (without losing quality)

The single biggest cost is **voice infrastructure at ₹1.20/min** when we rent it
from LiveKit's cloud — more than all the AI parts combined. We do **not** change
LiveKit and we do **not** touch the parts that make Arya sound good (speech and
AI engines stay exactly the same). We only change **where LiveKit runs**.

**The change: run LiveKit on one cheap VPS instead of renting LiveKit Cloud.**

LiveKit is open-source software. We keep using it — we just run it on our own
server (a Hostinger VPS at **₹799/month**) instead of paying LiveKit's per-minute
cloud rate. Everything Arya needs lives on that one box:

- the LiveKit voice server + phone (SIP) connection,
- Arya's "brain" program (the agent that runs the call),
- the dashboard and web service.

This works on a small VPS because the heavy AI parts — understanding speech,
generating the voice, and the AI reasoning — all run on **outside services**
(Sarvam and Groq), not on our server. Our VPS only manages the call and passes
audio through, which is light work.

**Hostinger KVM 2 — ₹799/month:** 2 CPU cores, 8 GB RAM, 100 GB storage, 8 TB
data transfer. Comfortably handles **~10–15 phone calls at the same time.**

**Why ₹799 barely moves the per-minute cost:** Arya is *multi-tenant* — one
server runs many hospitals at once. A single ₹799 box can host 20–40 small
clinics (which rarely have more than 1–2 calls going at once). Spread across
their combined minutes, the server cost per minute is almost nothing.

| Monthly minutes on one ₹799 box | Server cost per minute |
|---|---|
| 5,000 min | ₹0.16 |
| 15,000 min | ₹0.05 |
| 30,000 min | ₹0.03 |

**Result — cost per minute:**

| | LiveKit Cloud | One VPS (₹799) |
|---|---|---|
| Phone line (Plivo) | ₹0.60 | ₹0.60 |
| Voice infrastructure | ₹1.20 | ~₹0.05–0.16 |
| Speech understanding (Sarvam) | ₹0.50 | ₹0.50 |
| Speech voice (Sarvam) | ₹0.15 | ₹0.15 |
| AI brain (Groq) | ₹0.20 | ₹0.20 |
| SMS + buffer | ₹0.10 | ₹0.10 |
| **Total per minute** | **≈ ₹2.75** | **≈ ₹1.60–1.70** |
| Quality | Full | **Full (identical)** |

**We comfortably land under ₹2/minute** the moment a box is even lightly used —
with no drop in quality, and without changing LiveKit, the voice, or the AI.

Other quality-safe savings already built in:
- **Voice caching:** common phrases (greeting, confirmations) are generated once
  and reused, lowering the speech-voice cost below ₹0.15/min.
- **Smart AI routing:** the AI brain runs on the fast, low-cost tier first and
  only escalates when needed — keeping the AI cost near ₹0.20/min.

### Honest caveats of the one-VPS approach

1. **Call limit per box.** One ₹799 VPS handles ~10–15 simultaneous calls. A
   large hospital with many lines at once needs a bigger VPS (Hostinger KVM 4,
   ~₹1,599/month, 4 cores/16 GB) or a second box. We add boxes as volume grows —
   the per-minute cost stays the same.
2. **Backup server for critical use.** One VPS is a single point of failure — if
   it goes down, calls stop. For hospitals we recommend a second standby VPS
   (+₹799/month) for automatic failover once revenue justifies it. Small clinics
   can start on a single box.
3. **One-time setup.** Moving LiveKit onto our VPS (phone connection, security
   certificates, call routing) is a one-time engineering task.
4. **Renewal price.** ₹799 is the promotional rate; it renews around
   ₹1,400/month — still negligible spread across calls.

---

## 5. The Plans

Each plan is a monthly subscription that includes a block of talk-time minutes.
Extra minutes beyond the block are billed at the overage rate.

| Plan | Included talk-time | Monthly price | Effective ₹/min | Best for |
|---|---|---|---|---|
| **Starter** | up to 1,000 min | **₹6,999** | ₹7.00 | Single-doctor clinics |
| **Growth** | up to 2,500 min | **₹14,999** | ₹6.00 | Small clinics / polyclinics |
| **Professional** | up to 30,000 min | **₹1,34,999** | ₹4.50 | Mid-size hospitals |
| **Enterprise** | up to 60,000 min | **₹2,39,999** | ₹4.00 | Large hospitals |
| **Enterprise+** | up to 100,000 min | **₹3,49,999** | ₹3.50 | Multi-specialty groups |

**Overage** (extra minutes): billed at the plan's effective rate + ₹0.50/min.

All plans include: Malayalam + 5 other languages, 24/7 answering, unlimited
simultaneous calls, appointment booking, confirmation SMS, call transfer to
staff, and a dashboard with call logs and cost tracking.

---

## 6. Our profit on each plan

| Plan | Monthly price | Our cost | **Gross profit** | **Margin** |
|---|---|---|---|---|
| Starter | ₹6,999 | ~₹3,000 | **₹3,999** | **57%** |
| Growth | ₹14,999 | ~₹7,500 | **₹7,499** | **50%** |
| Professional | ₹1,34,999 | ~₹66,000 | **₹68,999** | **51%** |
| Enterprise | ₹2,39,999 | ~₹1,20,000 | **₹1,19,999** | **50%** |
| Enterprise+ | ₹3,49,999 | ~₹1,80,000 | **₹1,69,999** | **49%** |

We hold roughly a **50% gross margin across every plan** — small plans run on
managed cloud (higher cost, higher price), large plans run on our own
infrastructure (lower cost), so the margin stays consistent as customers grow.

---

## 7. The customer's return (ROI)

### Example: a mid-size hospital on the Professional plan

- **Pays us:** ₹1,34,999/month (30,000 minutes ≈ 10,000 calls).
- **Replaces:** 3–4 reception staff on phones → saves **₹60,000–₹80,000/month**
  in salaries, and now covers nights, Sundays, and holidays too.
- **Recovers missed calls:** if just 15% of calls were previously missed
  (~1,500 calls) and 40% of those would book (~600 appointments) at an average
  ₹500 consultation → **₹3,00,000/month in recovered revenue.**

**Net result for the hospital:** roughly ₹3,60,000–₹3,80,000/month in savings +
recovered revenue, against a ₹1,34,999 cost. **A return of more than 2.5× their
spend** — before counting better patient experience and zero hold times.

### The simple pitch to a customer

> "One missed call can be one lost patient. Arya answers every call, in
> Malayalam, day and night, and books the appointment on the spot — for less
> than the cost of a single receptionist."

---

## 8. Notes for the founders

- Prices are **indicative** and based on June 2026 provider rates; revisit if
  Plivo / LiveKit / Sarvam change pricing.
- Add **18% GST** on top of all prices.
- The gap between plan tiers matches real Kerala facility sizes (see section 3) —
  most clinics land in Starter/Growth, most private hospitals in
  Professional/Enterprise.
- A **one-time setup fee** (phone number provisioning, hospital data load, staff
  training) of ₹10,000–₹25,000 is recommended but not included above.
- Consider a **14-day free trial** (capped at ~300 minutes) to win first
  customers — our cost for that trial is under ₹900.
