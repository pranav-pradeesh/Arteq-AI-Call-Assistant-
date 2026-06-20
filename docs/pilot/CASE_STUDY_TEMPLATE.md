# Pilot Case Study — Template

> A one-to-two page proof artifact to produce after the first paid pilot. Buyers want
> evidence, not a demo. **All `__` are placeholders to fill with real measured data** —
> do not invent figures.

---

## `<Hospital Name>` — AI Voice Reception Pilot

**Profile:** `<type — e.g. 200-bed multi-speciality>`, `<city, state>` · Languages:
`<e.g. Malayalam, English>` · Pilot period: `__` to `__` (`__` weeks).

### The problem
`<e.g. Reception lines busy at peak OPD hours; missed calls after hours; patients
waiting on hold; staff overloaded with repetitive timing/booking questions.>`

Baseline (measured before go-live):
- Missed-call rate: `__%`
- Average hold time: `__`
- After-hours calls unanswered: `__/day`
- Reception staff cost for phones: `₹__/month`

### The deployment
- Kept existing landline; forwarded to Vobiz DID → Arya.
- `<N>` languages live; `<recordings on/off>`; WhatsApp confirmations `<on/off>`.
- Time to go-live: `__` days.

### Results (pilot period)

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Calls answered | `__%` | `__%` | `__` |
| Missed calls | `__/day` | `__/day` | `__` |
| Avg hold time | `__` | `~0s` | `__` |
| Appointments booked by AI | — | `__` | — |
| Containment (no human transfer) | — | `__%` | — |
| Booking accuracy | — | `__%` | — |
| After-hours calls handled | `0` | `__` | `__` |
| Patient CSAT | `__` | `__` | `__` |

### Economics
- Variable cost over pilot: `₹__` (`__` minutes × `₹__/min`)
- Estimated staff cost displaced/avoided: `₹__/month`
- **Net monthly saving / ROI:** `__`

### Quote
> "`<patient or hospital administrator quote>`" — `<name, title>`

### What we learned / next steps
`<honest notes: what worked, what was tuned (e.g. a language voice, a prompt), rollout
plan to more departments/sites.>`

---

## How to run the pilot (so the numbers are credible)
1. **Measure the baseline** for 1–2 weeks before go-live (don't skip this — it's the
   "before" column).
2. **Agree success criteria up front** (use the targets in `ACCURACY_BENCHMARK.md` §5).
3. Run **4–8 weeks**; review weekly; tune prompts/voices as needed.
4. Pull metrics from the dashboard (calls, containment, latency, bookings) + a short
   patient CSAT survey (WhatsApp).
5. Get a **named quote** and written permission to use the hospital's name/logo.
6. Publish this filled template + the filled `ACCURACY_BENCHMARK.md` as the sales proof.
