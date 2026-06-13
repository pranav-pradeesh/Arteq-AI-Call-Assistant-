# Arteq AI Call Assistant — Cost Model (per minute)

**Goal:** keep all-in cost **≤ ₹2/min** (hard max **₹3/min**), covering server/VPS,
telephony, and all API costs.

**Date:** 2026-06-03 · **FX used:** ₹85 / USD · **Tax loading:** 18% GST on
Indian vendors (Sarvam, Plivo); ~21% (≈3% forex + 18% GST) on USD vendors
(Groq, LiveKit).

> ⚠️ Rates verified from public pricing pages on 2026-06-03 and are **estimates**.
> Confirm with a written vendor quote before pricing customers. Telephony and
> token usage vary with call length, language, and conversation style.

---

## 1. Verified unit rates

| Service | Public rate | Notes |
|---|---|---|
| **Sarvam STT (Saaras)** | ₹30 / audio-hour = **₹0.50/min** | Billed per audio minute |
| **Sarvam TTS (Bulbul v3)** | ₹15–30 / 10k chars | ~₹0.0015–0.003 / char |
| **Groq LLM — llama-3.3-70b** | $0.59 / 1M in, $0.79 / 1M out | Premium quality. Free dev tier was pulled — assume paid. |
| **Groq LLM — llama-3.1-8b** | $0.05 / 1M in, $0.08 / 1M out | ~12× cheaper, but too weak at Malayalam — fallback only |
| **Together / Fireworks — llama-3.3-70b** | ~$0.88–0.90 / 1M flat | Drop-in 70b when Groq is unavailable |
| **OpenAI — gpt-4o-mini** | $0.15 / 1M in, $0.60 / 1M out | Budget; Malayalam quality must be A/B tested |
| **OpenAI — gpt-5 / 4.1 (full)** | $1.25+ / 1M in | Keeps quality; ~₹1.5/min, breaks ₹2 on phone |
| **LiveKit Cloud — agent session** | **$0.01/min ≈ ₹1.03/min** (tax-in) | **One per call — the dominant cost.** Free: 1,000 min/mo (Build), 5,000 (Ship $50/mo), 50,000 (Scale $500/mo) |
| **LiveKit Cloud — WebRTC participant** | $0.0005/min ≈ ₹0.05/min | Per end-user; 5k min/mo free |
| **LiveKit self-hosted (OSS)** | **₹0 marginal** | Runs on your server/VPS — zeroes the agent-session charge |
| **Plivo — inbound to India DID** | **₹0.60/min** | Phone callers dialing in |
| **Plivo — outbound to India mobile/landline** | **₹0.60/min** | Agent dials patient (reminders/campaigns) |
| **Plivo — SIP / Browser SDK** | **₹0.34/min** | Cheaper phone path |
| **Plivo — India DID rental** | ₹250 / number / month | Fixed, not per-minute |

**Usage assumptions per call-minute:** ~2 conversation turns; LLM input ≈ 7,000
tokens, output ≈ 600 tokens (prompt already shrunk for Groq TPM); agent speaks
≈ 200–300 chars of TTS; STT listens the full minute.

---

## 2. Per-minute build-up (tax-inclusive)

### Core AI + media (always present)

| Component | Pre-tax | +Tax |
|---|---|---|
| Sarvam STT | ₹0.50 | ₹0.59 |
| Sarvam TTS | ₹0.45 | ₹0.53 |
| **Groq 70b** | ₹0.39 | ₹0.47 |
| **Groq 8b (alt)** | ₹0.03 | ₹0.04 |
| **LiveKit Cloud** (agent session + participant) | ₹0.89 | **₹1.08** |
| **LiveKit self-hosted** | ₹0 | **₹0.00** |

> ⚠️ **LiveKit Cloud agent minutes ($0.01/min) are the single biggest line — bigger
> than STT.** Self-hosting the open-source LiveKit server (on the same hospital
> server / clinic VPS) zeroes it. The model below shows both.

- **Core, LiveKit Cloud + 70b** = 0.59 + 0.53 + 0.47 + 1.08 = **₹2.67/min** ❌
- **Core, LiveKit Cloud + 8b**  = 0.59 + 0.53 + 0.04 + 1.08 = **₹2.24/min** ❌
- **Core, self-hosted LK + 70b** = 0.59 + 0.53 + 0.47 + 0   = **₹1.59/min** ✅
- **Core, self-hosted LK + 8b**  = 0.59 + 0.53 + 0.04 + 0   = **₹1.16/min** ✅

### LLM line — multi-provider chain (≤₹2/min implications)

The agent now runs a **resilient multi-provider chain** (`LLM_PROVIDER_ORDER`):
any OpenAI-compatible host with a key joins, Sarvam is always the last resort, so
a single-vendor outage (e.g. Groq's dev tier being pulled) is non-fatal. The
**LLM ₹/min** depends on which model is primary (7,000 in + 600 out tokens/min):

| Primary model | LLM ₹/min (tax-in) |
|---|---|
| Groq llama-3.3-70b (paid) | ₹0.47 |
| Together / Fireworks llama-3.3-70b | ₹0.70 |
| OpenAI gpt-4o-mini | ₹0.15 |
| OpenAI gpt-5 / 4.1 (full) | ₹1.52 |

**The ≤₹2/min constraint vs. 70b quality is a real tension** (core = self-hosted
LiveKit + Sarvam STT/TTS = ₹1.12/min):

| Channel | 70b (Groq) | 70b (Together) | gpt-4o-mini |
|---|---|---|---|
| Browser | ₹1.59 ✅ | ₹1.82 ✅ | ₹1.27 ✅ |
| Phone (SIP) | ₹1.99 ✅ | ₹2.22 ❌ | ₹1.67 ✅ |
| Phone (DID) / outbound | ₹2.30 ❌ | ₹2.53 ❌ | ₹1.98 ✅ |

**To stay ≤₹2/min on *every* channel** while Sarvam STT/TTS is in use, the primary
must be a mini-class model (~₹0.15) — which carries Malayalam quality risk and
**must be A/B tested**. To keep **70b quality AND ≤₹2**: either (a) restrict to
browser + Plivo **SIP** (not DID), or (b) **self-host STT/TTS** — that drops core
to ~₹0.44/min, after which 70b fits under ₹2 on every channel including outbound.

### Channel add-on

| Channel | +Tax cost |
|---|---|
| Browser / WebRTC (`/talk`) — inbound | **₹0.00** |
| **Inbound** phone via Plivo SIP | **₹0.40** |
| **Inbound** phone via Plivo DID | **₹0.71** |
| **Outbound** call via Plivo (reminders/campaigns) | **₹0.71** |

### Server add-on

| Who | Server cost | Per-min (at 10k min/mo) |
|---|---|---|
| **Hospital** (own server) | ₹0 marginal | **₹0.00** |
| **Clinic** (cheap VPS — Oracle Always-Free ₹0, or Hetzner ~₹350/mo) | shared | **₹0.00–0.05** |

---

## 3. Scenario matrix (all-in ₹/min, tax-inclusive, **8b LLM**)

> LiveKit Cloud (past free tier) adds **₹1.08/min** to every row below and pushes
> all phone scenarios over ₹3. **Production must self-host LiveKit.** Matrix
> assumes self-hosted LiveKit + 8b LLM.

| Scenario | Direction | Total | vs ₹2 | vs ₹3 |
|---|---|---|---|---|
| **Hospital · browser** | inbound | **₹1.16** | ✅ | ✅ |
| **Hospital · phone (SIP)** | inbound | **₹1.56** | ✅ | ✅ |
| **Hospital · phone (DID)** | inbound | **₹1.87** | ✅ | ✅ |
| **Hospital · outbound** | outbound | **₹1.87** | ✅ | ✅ |
| **Clinic · browser** | inbound | **₹1.21** | ✅ | ✅ |
| **Clinic · phone (SIP)** | inbound | **₹1.61** | ✅ | ✅ |
| **Clinic · phone (DID)** | inbound | **₹1.92** | ✅ | ✅ |
| **Clinic · outbound** | outbound | **₹1.92** | ✅ | ✅ |

*With the premium 70b model add ₹0.43/min to each row — still ≤₹2.3, under the ₹3 cap.*

---

## 4. Verdict

- **LiveKit is the make-or-break cost.** On LiveKit Cloud, the $0.01/min agent
  session alone makes every phone scenario breach ₹2 (and most breach ₹3 with
  telephony). **Self-host LiveKit** (free OSS, runs on the server you already have)
  and the whole product drops to **₹1.16–1.92/min** — inbound *and* outbound, both
  tiers — comfortably under ₹2.
- **Use LiveKit Cloud only for the free tier** (Build: 1,000 agent-min/mo) during
  pilots, then move to self-hosted before volume.
- **Outbound calls** (reminders, confirmations, campaigns — already built) cost the
  same AI core + Plivo outbound ₹0.71/min → **₹1.87–1.92/min** self-hosted. Fits budget.
- **8b LLM** keeps margin wide; **70b** is affordable as a premium tier within ₹3.

**Two non-negotiables to stay ≤₹2/min: (1) self-host LiveKit, (2) use llama-3.1-8b.**

---

## 5. Optimization levers (to stay ≤ ₹2/min)

| Lever | Saving /min | Trade-off |
|---|---|---|
| **Self-host LiveKit (OSS) instead of Cloud** | **−₹1.08** | Run on existing server; biggest single win |
| **LLM 70b → 8b** | −₹0.43 | Slightly less nuanced phrasing |
| **Plivo SIP instead of inbound DID** | −₹0.31 | Requires SIP trunk setup |
| **VAD-gated STT (only stream speech)** | −₹0.20–0.30 | Minor engineering |
| **Pre-render static TTS prompts (greeting/menu/after-hours)** | −₹0.10–0.20 | Cache audio files |
| **Rule-based FAQ/menu (skip LLM on common turns)** | varies | Fast-path routing |
| **Shorter agent replies (tighter TTS)** | −₹0.15 | Already capped at 2 sentences |
| **Browser-first for clinics** | −₹0.40 to −₹0.71 | No PSTN reach |
| **Self-host STT+TTS on hospital GPU/CPU** | −₹1.12 | Whisper/IndicTTS; quality test needed |
| **Groq free tier (demo only)** | −₹0.04 to −₹0.47 | Rate-limited; not for production concurrency |

**Recommended production config to guarantee ≤₹2/min:**
**Self-hosted LiveKit + 8b LLM + Plivo SIP**, browser-first where possible. Reserve
70b for a premium tier billed nearer the ₹3 ceiling. Hospitals additionally self-host
STT/TTS → near-telephony-only cost (~₹0.44/min on phone, ~₹0/min on browser).

---

## 6. Fixed monthly costs (NOT per-minute)

| Item | Cost / month | Applies to |
|---|---|---|
| Plivo India DID rental | ₹250 / number | Phone-enabled tenants |
| VPS — **Oracle Cloud Always-Free** | **₹0** (4 ARM cores, 24 GB, forever) | Clinics — hosts app + LiveKit + Postgres |
| VPS — Hetzner (alt) | ~₹350 | Clinics wanting more headroom |
| Cloud server (Railway, current) | ~₹1,700 | Move off this for cost |
| **LiveKit Cloud** (if not self-hosting) | $0 (Build, 1k min) → $50 (Ship, 5k) → $500 (Scale, 50k) | Avoid at scale; self-host instead |
| Sarvam / Groq | pay-as-you-go | No fixed minimum |
| **Claude Max 5× (developer, India)** | **≈ ₹11,210** | Build + maintenance only — **not a per-call cost** |
| Claude Max 20× (heavy dev, India) | ≈ $200 + forex + 18% GST ≈ ₹20,000 | Optional, intensive build phases |

> **Claude Max note:** Anthropic has no India-specific INR plan; billed in USD,
> so an Indian card adds ~2–3% forex fee **and** 18% GST. Max 5× ≈ **₹11,210/mo**
> as of May 2026. This is a **one-developer tooling cost during build/maintenance**,
> not a runtime cost charged per call. Amortized across many tenants and minutes it
> is negligible per-minute; treat it as fixed opex.

---

## 7. Hospital vs Clinic summary

- **Hospitals** run the worker **+ self-hosted LiveKit (and optionally STT/TTS)** on
  **their own servers** → drop the ₹1.08/min LiveKit Cloud line entirely. Result:
  **~₹0/min browser, ~₹0.44/min phone** if they also self-host STT/TTS, otherwise
  ₹1.16–1.87/min. Outbound included.
- **Clinics** have no servers → run app + **self-hosted LiveKit + Postgres** on a
  single **Oracle Always-Free VPS (₹0)** or cheap Hetzner box. Browser ~₹1.21/min,
  phone ~₹1.61–1.92/min, outbound ~₹1.92/min — all under ₹2 with 8b + self-hosted LiveKit.
- **Both** must self-host LiveKit; using LiveKit Cloud past the free tier breaks the
  budget on its own (+₹1.08/min).

---

*Figures are estimates for planning only. Verify Sarvam, Plivo, Groq, LiveKit, and
Anthropic pricing directly before committing to customer rates.*
