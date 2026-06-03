# Arteq AI Call Assistant — Audit & Cost Decision Document

**Prepared for:** Founder / decision-maker
**Date:** 2026-06-03
**Purpose:** Choose a deployment + cost configuration for multispeciality hospitals
**and** small clinics.

> **Assumptions & disclaimer:** All vendor rates were verified from public pricing
> pages on 2026-06-03 and are planning estimates. FX = ₹85/USD. Taxes: 18% GST on
> Indian vendors (Sarvam, Plivo); ~21% (≈3% forex + 18% GST) on USD vendors (Groq,
> LiveKit, Anthropic). Confirm with written vendor quotes before signing customer
> contracts. Call-volume figures are illustrative — actual cost scales linearly with
> minutes used.

---

## 1. TL;DR — recommendation

| Segment | Recommended config | All-in cost | Monthly (typical) |
|---|---|---|---|
| **Flagship hospital** (own servers) | Self-host LiveKit; Sarvam APIs; Groq 8b; route via hospital SIP trunk | **₹1.16–1.56/min** | **~₹1.0–2.0 lakh** |
| **Flagship hospital** (cost-optimised) | + self-host STT/TTS on GPU | **₹0.44–0.75/min** | **~₹40k–63k** |
| **Small clinic** (no servers) | Oracle Always-Free VPS; self-host LiveKit; Sarvam APIs; Groq 8b; browser-first | **₹1.16–1.56/min** | **~₹7k–10k** |

**Two technical decisions drive 80% of the cost:**
1. **Self-host LiveKit** (open-source) instead of LiveKit Cloud → saves **₹1.08/min**.
2. **Use the small LLM** (llama-3.1-8b) instead of 70b → saves **₹0.43/min**.

Both are configuration choices, not new development. With both applied, every
scenario — hospital or clinic, inbound or outbound, phone or browser — stays **under
₹2/min**, meeting the target.

---

## 2. What the system does

A Malayalam-first AI voice receptionist ("Arya") that answers and makes phone calls
for hospitals and clinics:

- **Inbound:** answers patient calls, books/reschedules/cancels appointments, gives
  doctor schedules & department info, routes emergencies, handles IVR digits, sends
  location SMS, transfers to departments.
- **Outbound:** appointment reminders, confirmations, callbacks, post-visit follow-ups,
  and bulk campaigns.
- **Channels:** phone (via Plivo/SIP) **and** in-browser voice (`/talk` web link — no
  telephony cost).
- **Multi-tenant:** one deployment serves many hospitals/clinics, each with its own
  persona, doctors, departments, fees, FAQs, and language.
- **Stack:** LiveKit (audio transport) → Sarvam (Malayalam STT + TTS) → Groq (LLM
  reasoning) → Postgres (data). Admin dashboard for configuration.

The product is **fully built and deployed**; all 14 phases are code-complete with a
passing test suite.

---

## 3. Cost drivers (per minute, tax-inclusive)

| Component | LiveKit Cloud | **Self-hosted / optimised** | Notes |
|---|---|---|---|
| LiveKit (audio transport) | **₹1.08** | **₹0.00** | Biggest single line on Cloud; OSS server is free |
| Sarvam STT | ₹0.59 | ₹0.59 (API) or ₹0 (self-host) | Per audio minute |
| Sarvam TTS | ₹0.53 | ₹0.53 (API) or ₹0 (self-host) | Per character |
| Groq LLM — 70b | ₹0.47 | — | Premium |
| Groq LLM — 8b | **₹0.04** | **₹0.04** | Sufficient for receptionist |
| Telephony — Plivo SIP | ₹0.40 | ₹0.40 (or ₹0 via hospital trunk) | Phone only |
| Telephony — Plivo DID inbound/outbound | ₹0.71 | ₹0.71 | Phone only |
| Browser channel | ₹0.00 | ₹0.00 | No PSTN cost |

---

## 4. Deployment configurations (pros / cons)

### Config A — Turnkey (Sarvam APIs + self-hosted LiveKit + 8b)
*Recommended default for hospitals and clinics.*

**Per-minute:** ₹1.16 core, ₹1.56 phone (SIP), ₹1.87 outbound.

| Pros | Cons |
|---|---|
| Best Malayalam STT/TTS quality (Sarvam) | Sarvam usage scales with minutes (~₹1.12/min) |
| Low operational burden — no ML model ops | Dependency on Sarvam uptime/pricing |
| Self-hosted LiveKit kills the dominant cost | Need to run/maintain a LiveKit server |
| Under ₹2/min everywhere | |

### Config B — Cost-optimised (self-host STT + TTS + LiveKit + 8b)
*For high-volume hospitals with their own GPU servers.*

**Per-minute:** ₹0.44 phone (SIP), ₹0.75 outbound, ~₹0 browser.

| Pros | Cons |
|---|---|
| Cheapest possible — ~₹0.44/min on phone | Needs GPU server + ML ops expertise |
| No per-minute STT/TTS vendor cost | Open Malayalam TTS quality below Bulbul — must test |
| Data stays on-premise (privacy) | Higher one-time setup + maintenance |
| Best at very high volume | Quality/latency tuning effort |

### Config C — LiveKit Cloud (NOT recommended past pilot)

| Pros | Cons |
|---|---|
| Zero LiveKit ops; free tier for pilots (1,000 min/mo) | **+₹1.08/min** — breaks the ₹2 budget on its own |
| Good for first demos | Phone scenarios exceed ₹3/min |
| | Vendor lock-in, USD billing |

---

## 5. Cheaper options per component (pros / cons)

### 5.1 Audio transport — LiveKit
| Option | Cost | Pros | Cons |
|---|---|---|---|
| LiveKit Cloud | ₹1.08/min | No ops | Most expensive line; breaks budget at scale |
| **Self-host LiveKit (OSS)** ✅ | ₹0/min | Free, no per-min charge, removes region latency | Must run a server (already needed) |

### 5.2 LLM — Groq
| Option | Cost | Pros | Cons |
|---|---|---|---|
| llama-3.3-70b | ₹0.47/min | Most nuanced replies | 12× pricier; free tier rate-limited |
| **llama-3.1-8b** ✅ | ₹0.04/min | Cheap, fast, fine for receptionist intents | Slightly less nuanced phrasing |
| Self-host LLM | ₹0 marginal | On-prem, private | Needs strong GPU; complex; rarely worth it vs Groq 8b |

### 5.3 STT / TTS — Sarvam
| Option | Cost | Pros | Cons |
|---|---|---|---|
| Sarvam APIs | ₹1.12/min | Best Malayalam quality, no ops | Per-minute vendor cost |
| Self-host (Whisper / IndicTTS) | ₹0 marginal | Free at scale, on-prem | GPU + ML ops; quality below Sarvam — must validate |
| **Pre-render static prompts** ✅ | −₹0.10–0.20/min | Greeting/menu/after-hours rendered once, replayed | Only covers fixed lines |
| **VAD-gated STT** ✅ | −₹0.20–0.30/min | Bill STT only on actual speech | Minor engineering |

### 5.4 Telephony — Plivo
| Option | Cost | Pros | Cons |
|---|---|---|---|
| Plivo DID inbound/outbound | ₹0.60/min | Easy, reliable India coverage | Per-minute + ₹250/number rental |
| **Plivo SIP** ✅ | ₹0.34/min | Cheaper than DID | Needs SIP trunk setup |
| **Hospital's existing PRI/SIP trunk** ✅✅ | ~₹0/min | Near-free; uses lines they already pay for | Only large hospitals have one |
| **Browser / WhatsApp channel** ✅ | ₹0/min | No PSTN cost at all | No phone-number reach |
| Alt providers (Exotel, Knowlarity, MyOperator) | varies | May be cheaper for domestic; negotiate volume | Migration effort |

### 5.5 Server / VPS
| Option | Cost | Pros | Cons |
|---|---|---|---|
| Hospital's own server | ₹0 marginal | Free, on-prem, private | Hospital must provide |
| **Oracle Cloud Always-Free** ✅ | ₹0 forever | 4 ARM cores, 24 GB RAM — fits a clinic + LiveKit + DB | Capacity limits at high volume |
| Hetzner VPS | ~₹350/mo | Cheap, more headroom | Outside India (latency minor) |
| Railway (current) | ~₹1,700/mo | Convenient | Pricier; move off for production |

### 5.6 Database
| Option | Cost | Pros | Cons |
|---|---|---|---|
| **Self-host Postgres on same VPS** ✅ | ₹0 extra | Free, simple | Shares VPS resources |
| Supabase free tier | ₹0 (≤500 MB) | Managed, easy | Outgrows free tier with scale |
| Managed Postgres (paid) | ₹500+/mo | Backups, HA | Unnecessary at this scale |

---

## 6. Monthly cost — flagship multispeciality hospital (Kozhikode)

**Assumed volume (appointment + enquiry line):** ~1,000 inbound calls/day @ 3 min +
~500 outbound/day @ 2 min = **~120,000 minutes/month** (90k inbound, 30k outbound).
Hospital self-hosts LiveKit on own servers.

| Config | Inbound | Outbound | + DID | **Monthly total** |
|---|---|---|---|---|
| **A — Sarvam APIs** | 90k × ₹1.56 = ₹1,40,400 | 30k × ₹1.87 = ₹56,100 | ₹750 | **≈ ₹1.97 lakh** |
| **B — self-host STT/TTS** | 90k × ₹0.44 = ₹39,600 | 30k × ₹0.75 = ₹22,500 | ₹750 | **≈ ₹63,000** |
| **B + own SIP trunk** | telephony → ~₹0 | telephony → ~₹0 | — | **≈ ₹40,000** |

**Volume sensitivity (Config A):** 60k min ≈ ₹1.0 lakh · 120k min ≈ ₹2.0 lakh ·
200k min ≈ ₹3.3 lakh.

**Value framing:** ₹2 lakh/month replaces a 4–6 seat reception/call-center desk
(≈ ₹1.5–3 lakh/month in salaries), running 24×7 in Malayalam, never missing a call.
Config B makes it a decisive cost win.

---

## 7. Monthly cost — small clinic

**Assumed volume:** ~80 calls/day @ 2.5 min = **~6,000 minutes/month**. No servers →
Oracle Always-Free VPS (₹0) hosting app + LiveKit + Postgres.

| Channel | Min/mo | ₹/min | + DID | **Monthly total** |
|---|---|---|---|---|
| **Browser-first** | 6,000 | ₹1.21 | — | **≈ ₹7,260** |
| **Phone (Plivo SIP)** | 6,000 | ₹1.56 | ₹250 | **≈ ₹9,610** |

A clinic runs for **under ₹10,000/month**, often under ₹7,500 on the browser channel.
This is the segment where the Oracle free VPS + browser channel makes margins very high.

---

## 8. Fixed / one-time costs

| Item | Cost | Who pays | Notes |
|---|---|---|---|
| Plivo DID rental | ₹250 / number / mo | Phone tenants | Recurring |
| VPS (clinic) | ₹0 (Oracle) – ₹350 (Hetzner) | Clinic | Recurring |
| Server / GPU (hospital) | own | Hospital | One-time if buying GPU for Config B |
| **Claude Max 5× (developer)** | ≈ ₹11,210/mo | **Us (Arteq), not the customer** | Build + maintenance tooling; USD-billed |
| Integration & onboarding | project-based | Customer | One-time per deployment |

---

## 9. Risks & dependencies

- **Vendor pricing/uptime:** Sarvam, Groq, Plivo, LiveKit can change rates. Self-hosting
  (Config B) reduces exposure.
- **LLM rate limits:** Groq free tier caps concurrency; production needs paid tier
  (8b cost is still negligible).
- **Network/region latency:** LiveKit Cloud showed signal latency from India; self-hosting
  in-region fixes this.
- **Open-model quality (Config B):** self-hosted Malayalam TTS must be quality-tested
  against Sarvam Bulbul before committing.
- **Concurrency:** server must be sized for peak concurrent calls (a flagship hospital
  may see 20–40 simultaneous calls at peak).

---

## 10. Decision matrix

| If the customer is… | Choose | Why |
|---|---|---|
| Flagship hospital, has servers, wants quality + low ops | **Config A** | Best quality, ~₹2 lakh/mo, under ₹2/min |
| Flagship hospital, high volume, has GPU + IT team | **Config B** | ~₹40–63k/mo, lowest cost, on-prem privacy |
| Small clinic, no IT | **Config A on Oracle free VPS, browser-first** | Under ₹10k/mo, near-zero infra |
| Any customer, pilot/demo only | LiveKit Cloud free tier | 1,000 free min/mo; migrate before scaling |

**Universal rule:** always self-host LiveKit and use the 8b LLM in production. These
two choices alone keep every deployment under the ₹2/min target.

---

*Figures are planning estimates as of 2026-06-03. Verify Sarvam, Plivo, Groq, LiveKit,
and Anthropic pricing directly before committing to customer contracts.*
