# Accuracy Benchmark — Methodology & Results Template

> The single most common technical objection from a hospital is: *"How accurate is it
> on real, noisy phone calls in our language?"* This document defines a **repeatable,
> honest measurement** of that — and a results table to fill from a real pilot.
>
> **Numbers below are placeholders (`__`).** They must be populated from an actual
> measured run. Do **not** ship fabricated figures to a customer — a real, modest
> number beats an impressive fake one the moment it's tested.

---

## 1. What we measure

| Metric | Definition | Why it matters |
|---|---|---|
| **STT WER** | Word Error Rate of the speech-to-text vs human transcript | Core listening accuracy |
| **Language detection accuracy** | % of calls where the spoken language is correctly detected | Drives whole-call correctness |
| **Intent accuracy** | % of turns where the agent's understood intent matches human label | Did it understand the ask? |
| **Booking accuracy** | % of booking attempts producing the **correct** appointment (right doctor/date/time) | The money metric |
| **Containment rate** | % of calls fully handled without human transfer | Staffing ROI |
| **Task success** | % of calls where the caller's goal was achieved | End-to-end quality |
| **Median / P95 latency** | Caller-stops-speaking → agent-starts-speaking | Perceived responsiveness |
| **False-emergency / missed-emergency** | Emergency mis-handling rate | Patient safety |

## 2. Test set construction (must reflect reality)

- **Real telephony audio at 8 kHz** (not studio mic) — the production codec.
- **Per language** the hospital serves; minimum **50 calls/language** for a first read,
  200+ for confidence.
- Mix of: clean speech, background noise, code-switching (Manglish/Hinglish),
  elderly/soft speakers, fast speakers.
- Scenarios: new booking, reschedule, cancel, doctor/timing query, fee query,
  after-hours, emergency, off-topic (should be declined).
- **Hold out** the test set from any prompt tuning.

## 3. Procedure

1. Collect or record consented call audio (see `../compliance/CALL_RECORDING_CONSENT.md`).
2. Produce **human reference transcripts** and **human intent/outcome labels**.
3. Run audio through the production pipeline; capture transcript, detected language,
   intents, tool calls, latency (`latency_avg_ms`), and outcomes.
4. Compute metrics per language and overall. Two annotators on a 10% sample to check
   labelling agreement.
5. Record environment: model versions (STT/LLM/TTS), date, commit SHA.

## 4. Results template (fill from real pilot)

**Run metadata:** date `__` · commit `__` · STT `saarika v2.5` · LLM `gemini-2.0-flash` (+ sarvam-30b fallback) · TTS `bulbul v3`

| Language | Calls | STT WER ↓ | Lang-detect ↑ | Intent acc ↑ | Booking acc ↑ | Containment ↑ | Median latency ↓ |
|---|---:|---:|---:|---:|---:|---:|---:|
| Malayalam | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |
| Hindi | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |
| Tamil | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |
| Telugu | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |
| Kannada | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |
| English | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |
| **Overall** | `__` | `__%` | `__%` | `__%` | `__%` | `__%` | `__ ms` |

**Safety:** missed-emergency `__` / total emergencies · false-emergency `__` / total.

## 5. Targets (proposed acceptance bar for go-live)

| Metric | Target |
|---|---|
| Booking accuracy | ≥ 95% |
| Containment rate | ≥ 70% |
| Language detection | ≥ 95% |
| Median latency | ≤ 1,200 ms |
| Missed emergencies | **0** |

> Targets are proposals; agree the acceptance bar with the hospital before the pilot so
> "success" is defined up front.

## 6. Reproducibility
Keep the labelled test set, the run outputs, and this filled table under version control
(excluding raw PII audio, which stays in the secured store). Re-run on each model/prompt
change to catch regressions — complements the call-flow regression tests.
