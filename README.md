# Arteq Hospital Voice Agent

> **A production-grade multilingual AI voice receptionist for Kerala hospitals and clinics.**
>
> One codebase. Multi-tenant. Hospitals get the full feature set; clinics get a lean, affordable version.

**Stack:** LiveKit (WebRTC/SIP) → Sarvam STT + TTS → Groq LLaMA 70B → PostgreSQL (Supabase)  
**Languages:** Malayalam, Hindi, Tamil, Kannada, Telugu, English, Manglish (auto-detected, no configuration needed)

---

## Table of Contents

1. [Architecture](#architecture)
2. [Local Development](#local-development)
3. [Running the App](#running-the-app)
4. [Testing](#testing)
5. [Admin Dashboard](#admin-dashboard)
6. [Production Deployment (Render)](#production-deployment-render)
7. [Post-Deploy Checklist](#post-deploy-checklist)
8. [Multi-Tenant Setup](#multi-tenant-setup)
9. [Tier System: Hospital vs Clinic](#tier-system-hospital-vs-clinic)
10. [Telephony Setup](#telephony-setup)
11. [Cost Analysis & ₹2/min Reality Check](#cost-analysis)
12. [Monitoring & Observability](#monitoring--observability)
13. [Scaling Playbook](#scaling-playbook)
14. [Security Checklist](#security-checklist)
15. [Troubleshooting](#troubleshooting)
16. [Project Structure](#project-structure)

---

## Architecture

```
CALLER (phone) ──► PLIVO DID ──► Plivo webhook ──► /api/v1/call/inbound/{slug}
                                                         │
                                                    FastAPI returns PCML
                                                    (SIP forward instruction)
                                                         │
                                                         ▼
                                              LiveKit SIP Inbound Trunk
                                              Room: "{slug}-call-{uuid}"
                                                         │
                                                    Dispatch Rule
                                                         │
                                                         ▼
                                              LiveKit Agent Worker (livekit_agent.py)
                                                         │
                                          ┌──────────────┼──────────────┐
                                          ▼              ▼              ▼
                                     Silero VAD    Sarvam STT     Sarvam TTS
                                     (turn det.)  (Saaras v3)   (Bulbul v3)
                                                       │
                                                       ▼
                                               Groq LLaMA 70B
                                               (function calls)
                                                       │
                                            ┌──────────┴──────────┐
                                            ▼                     ▼
                                      asyncpg (DB)          Plivo SMS
                                      book/cancel/callback  confirmation
```

**Two separate Render services:**
- **`arteq-voice-agent`** — FastAPI web server (webhooks, health, token endpoint, admin dashboard)
- **`arteq-livekit-agent`** — LiveKit worker pool (handles every concurrent call room)

Both share the same PostgreSQL database (Supabase).

---

## Local Development

### Prerequisites

| Service | Purpose | Free tier |
|---------|---------|-----------|
| [LiveKit Cloud](https://cloud.livekit.io) | WebRTC rooms + SIP | Yes |
| [Sarvam AI](https://app.sarvam.ai) | STT (Saaras v3) + TTS (Bulbul v3) | Trial credits |
| [Groq](https://console.groq.com) | LLaMA 70B LLM | Free (rate-limited) |
| [Supabase](https://supabase.com) | PostgreSQL database | Free |

Plivo is only needed for production telephony (real phone calls). For browser testing, only the four above are required.

### Quick start — one command (Windows / macOS / Linux)

The launcher sets up everything (virtual environment, dependencies, a `.env`
with auto-generated secrets) and starts the server. A tester only needs to open
the page and talk.

```bash
# macOS / Linux
./start.sh --with-agent

# Windows
start.bat --with-agent

# or, any platform, directly:
python run.py --with-agent
```

What it does, in order: verifies Python → creates `.venv` → installs
`requirements.txt` → creates `.env` from `.env.example` (generating a strong
`DASHBOARD_JWT_SECRET` / `INTERNAL_API_KEY`) → starts the FastAPI web server →
starts the LiveKit agent worker (`--with-agent`) → opens
**http://localhost:8000/talk** in your browser.

Then add your `SARVAM_API_KEY`, `GROQ_API_KEY` and `LIVEKIT_*` keys to the
generated `.env` and restart. Without `--with-agent` only the web server runs
(the page loads but no agent answers).

| Flag | Effect |
|------|--------|
| `--with-agent` / `-a` | also run the LiveKit agent worker (full end-to-end) |
| `--agent-only` | run only the agent worker |
| `--no-browser` | don't auto-open the browser |
| `--no-install` | skip dependency install (fast restart) |
| `--port 8080` | override the web port |

### Self-diagnostic for testers (`doctor`)

One command checks everything and writes a copy-pasteable report. It sets up
the venv first, then verifies Python, every `.env` key (without printing
secrets), code imports, the control DB + tenant registry, and live Groq/Sarvam
reachability.

```bash
# any platform
python run.py doctor
```

It prints `[PASS]` / `[WARN]` / `[FAIL]` per check and writes the full report to
`arteq-diagnostic.log` in the project root. Testers: when something breaks, run
this and paste the contents of `arteq-diagnostic.log` — it never contains
secret values, only whether each is set.

### Headless live-voice test (`smoke-call`)

`doctor` proves every dependency is reachable; `smoke-call` proves the actual
voice loop works **without a human mic**. It boots the agent worker, joins a
room, lets the agent (Arya) dispatch, and verifies she speaks back.

```bash
python run.py smoke-call            # uses the "default" tenant
python run.py smoke-call <slug>     # test a specific tenant
```

PASS = agent joined and produced audio. It logs the captured greeting
transcript (Malayalam) and writes `arteq-smokecall.log`; the agent-side log is
`arteq-worker.log`. If it FAILs, `arteq-worker.log` shows the agent-side cause
(STT/LLM/TTS keys, LiveKit dispatch, etc.).

### Manual setup (alternative)

```bash
git clone <your-repo-url>
cd Arteq-AI-Call-Assistant-

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — minimum required keys for local dev:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
#   SARVAM_API_KEY
#   GROQ_API_KEY
#   DATABASE_URL  (from Supabase → Settings → Database → Connection string)
#   DASHBOARD_ADMIN_PASSWORD   (any string, >= 12 chars)
#   DASHBOARD_JWT_SECRET       (run: python -c "import secrets; print(secrets.token_hex(32))")
```

### Database

Migrations run **automatically on every server startup** and are fully
idempotent — for Supabase or a local Postgres you don't need to run anything by
hand. To apply them manually, paste each file in `migrations/versions/*.sql`
(in order) into the Supabase SQL Editor.

The schema seeds a demo hospital: **slug `demo`**, ID
`00000000-0000-0000-0000-000000000001`.

> **Local Postgres / Docker:** set `DB_SSL=disable` (or rely on `DB_SSL=auto`,
> which disables SSL for `localhost`/docker hosts and requires it for Supabase).

---

## Running the App

The one-command launcher above is the recommended path. To run the two
processes manually in separate terminals:

```bash
# Terminal 1 — FastAPI web server (serves the /talk voice client)
make dev            # → http://localhost:8000

# Terminal 2 — LiveKit agent worker (Arya)
make agent          # connects to LiveKit Cloud, auto-joins rooms
```

Or with Docker (web + agent + Postgres + Redis, mirroring production):

```bash
docker compose up --build
```

### Talk to the agent in your browser

1. Open **http://localhost:8000/talk** (the launcher opens it automatically;
   `/` also redirects here).
2. Press **Start call** and allow microphone access.
3. Talk. Arya responds in Malayalam by default and auto-detects your language
   (English, Hindi, Tamil, Kannada, Telugu, Manglish).

The built-in client fetches a token from `/api/v1/livekit/token`, joins the
room over WebRTC, captures your mic and plays Arya's audio — no external tools
needed. (The agent worker must be running for Arya to answer.) The same
`/talk` page works on the deployed Render service.

---

## Testing

### Smoke tests (no live API calls)

```bash
make test
# or: pytest tests/test_smoke.py -v
```

These tests verify:
- Settings import and defaults
- Greeting text builder
- Slot date/time parsing (valid + invalid inputs)
- SMS service graceful degradation (returns `False` without Plivo keys)
- In-memory cache CRUD + TTL expiry
- `HospitalContext.hours_for_day()` logic
- Slug derivation

All tests run in under a second, no DB or API keys required.

### Integration tests

```bash
pytest tests/ -v --tb=short   # runs all test files
```

### Manual end-to-end test flow

1. Start both services (`make -j dev agent`)
2. Open the admin dashboard: `http://localhost:8000/admin/`
3. Log in with `DASHBOARD_ADMIN_PASSWORD`
4. Navigate to the demo hospital → **Telephony** tab — verify Voice AI shows green checkmarks
5. Use LiveKit Playground to place a browser call
6. After the call, check **Overview → Recent Calls** for the logged entry

---

## Admin Dashboard

```
http://localhost:8000/admin/
```

Login with `DASHBOARD_ADMIN_PASSWORD` (default in dev: `admin`).

### Tabs

| Tab | Purpose |
|-----|---------|
| **Overview** | Live call stats, recent call log, WebSocket URL |
| **Settings** | Hospital name, hours, slug, tier (clinic/hospital) |
| **Departments** | CRUD for OPD, ICU, Pharmacy, Lab, etc. |
| **Doctors** | CRUD for doctors with weekly schedule slots |
| **Billing** | Fee ranges per consultation type |
| **Emergency** | Priority-ranked emergency contacts |
| **FAQs** | Structured Q&A the AI uses for caller questions |
| **Appointments** | View/confirm/cancel all bookings with status filter |
| **Callbacks** | Pending callback requests from callers |
| **Knowledge Base** | Free-form text: parking, policies, special clinics, etc. |
| **Telephony** | Config status checklist, SIP setup trigger |

### REST API

All dashboard endpoints are under `/admin/` and require a Bearer JWT token.

```bash
# Login
POST /admin/login  {"password": "your-password"}
# -> {"access_token": "...", "token_type": "bearer"}

# Key endpoints
GET  /admin/hospitals
GET  /admin/hospitals/{id}
PUT  /admin/hospitals/{id}
GET  /admin/hospitals/{id}/appointments?status=requested&limit=50
GET  /admin/hospitals/{id}/callbacks?status=pending
GET  /admin/telephony/status?hospital_id={id}
POST /admin/sip/setup
POST /admin/hospitals/wizard          # one-shot onboarding
```

Full API docs available at `http://localhost:8000/docs`.

---

## Production Deployment (Render)

### Step 1: Push to GitHub

```bash
git push origin main
```

### Step 2: Create Render Blueprint

1. Log in to [Render](https://render.com)
2. **New → Blueprint**
3. Connect your GitHub repo
4. Select `render.yaml` — Render auto-creates both services

### Step 3: Set secrets in Render Dashboard

For each service, go to **Environment** and set these `sync: false` secrets:

| Variable | Where to get it |
|----------|----------------|
| `SARVAM_API_KEY` | [app.sarvam.ai](https://app.sarvam.ai) → API Keys |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) |
| `DATABASE_URL` | Supabase → Settings → Database → URI |
| `LIVEKIT_URL` | LiveKit Cloud → your project → Settings |
| `LIVEKIT_API_KEY` | LiveKit Cloud → your project → Settings |
| `LIVEKIT_API_SECRET` | LiveKit Cloud → your project → Settings |
| `DASHBOARD_ADMIN_PASSWORD` | choose >= 12 char password |
| `DASHBOARD_JWT_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `INTERNAL_API_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `PLIVO_AUTH_ID` | [console.plivo.com](https://console.plivo.com) → Overview |
| `PLIVO_AUTH_TOKEN` | [console.plivo.com](https://console.plivo.com) → Overview |
| `PLIVO_PHONE_NUMBER` | Your provisioned India DID (E.164: +918047XXXXXX) |
| `STAFF_ALERT_PHONE` | Duty manager's mobile (E.164 format) |

> **Security rule:** Never store API keys in code, `.env` files committed to git, or `render.yaml`. Always use the Render secrets UI or `sync: false` env vars.

### Step 4: Deploy

Render auto-deploys on push. Monitor logs in the Render dashboard.

---

## Post-Deploy Checklist

After the first successful deploy, do these **once**:

```bash
# 1. Verify health
curl https://your-service.onrender.com/api/v1/health

# 1b. Talk to the agent in the browser (no phone needed):
#     open https://your-service.onrender.com/talk
#     Requires the arteq-livekit-agent worker to be running (it is, per render.yaml).

# 2. Run SIP trunk setup (creates LiveKit SIP inbound + outbound trunks)
#    Only required if Plivo telephony is configured.
curl -X POST https://your-service.onrender.com/admin/sip/setup \
  -H "Authorization: Bearer <your-JWT>"

# 3. From the response, copy livekit_sip_outbound_trunk_id
#    -> Set LIVEKIT_SIP_OUTBOUND_TRUNK_ID in Render env vars
#    -> Redeploy both services

# 4. Get your SIP host from LiveKit Cloud -> SIP -> Inbound Trunks
#    -> Set LIVEKIT_SIP_HOST in Render env vars
#    -> Redeploy both services

# 5. Configure Plivo webhook:
#    Plivo Console -> Phone Numbers -> your DID -> Inbound Call URL:
#    POST https://your-service.onrender.com/api/v1/call/inbound/{your-hospital-slug}

# 6. (Optional) BSNL/MTNL call forwarding from hospital landline:
#    Dial: **21*+918047XXXXXX#
#    This forwards all incoming calls to your Plivo DID.
```

---

## Multi-Tenant Setup

Each hospital is a separate row in the `hospitals` table with a unique `slug`. The agent automatically loads the correct hospital context based on the room/call name.

### Adding a new hospital

**Option A: Dashboard Wizard (recommended)**

```bash
POST /admin/hospitals/wizard
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Malabar Super Speciality Hospital",
  "name_ml": "മലബാർ സൂപ്പർ സ്പെഷ്യാലിറ്റി ഹോസ്പിറ്റൽ",
  "tier": "hospital",
  "address": "Kozhikode, Kerala",
  "phone": "+914952XXXXXX",
  "slug": "malabar-hospital",
  "provision_plivo_number": true,
  "departments": [
    {
      "name": "Cardiology",
      "name_ml": "ഹൃദ്രോഗം",
      "floor": "3rd Floor",
      "doctors": [
        {
          "name": "Dr. Ramesh Kumar",
          "specialty": "Cardiologist",
          "schedules": [
            {"day_of_week": 1, "start_time": "09:00", "end_time": "13:00", "room": "OP-3"},
            {"day_of_week": 3, "start_time": "14:00", "end_time": "18:00", "room": "OP-3"}
          ]
        }
      ]
    }
  ],
  "faqs": [
    {"category": "timing", "question": "What are OPD hours?", "answer": "8AM to 8PM Mon-Sat"}
  ],
  "emergency_contacts": [
    {"label": "Emergency", "label_ml": "ഇമർജൻസി", "phone": "+914952XXXXXX", "priority": 10}
  ]
}
```

Returns: `hospital_id`, `slug`, `plivo_number`, `bsnl_forward_code`

**Option B: Dashboard UI**

1. Go to `https://your-service.onrender.com/admin/`
2. All Hospitals → **+ Add Hospital**
3. Fill in basic info, Save
4. Click the hospital → add departments, doctors, schedules, FAQs via tabs
5. Telephony tab → Run SIP Setup when Plivo is configured

### Call routing

Room names follow the pattern `{slug}-call-{uuid8}` (set by the SIP dispatch rule).
The agent extracts the slug and loads the correct hospital context. No code changes needed per hospital.

---

## Tier System: Hospital vs Clinic

Set `tier` on each hospital row to control which features the AI exposes.

| Feature | Hospital | Clinic |
|---------|:--------:|:------:|
| Book / cancel appointments | Yes | Yes |
| Request callback | Yes | Yes |
| Doctor schedule lookup | Yes | Yes |
| Send location SMS | Yes | Yes |
| Emergency alert (staff SMS) | Yes | Yes |
| Department info & call transfer | Yes | No |
| Multi-doctor routing | Yes | No |
| Outbound confirmation calls | Yes | Configurable |
| Outbound follow-up calls | Yes | Off by default |

Clinics get a leaner prompt and fewer LLM tools, reducing token usage and call latency.

**Update via API:**
```bash
PUT /admin/hospitals/{id}
{"tier": "clinic"}
```

**Update via Dashboard:** Settings tab → Tier dropdown.

---

## Telephony Setup

### Call flow (production)

```
Patient dials hospital landline
  -> BSNL/MTNL call forward (**21*<plivo_number>#) from hospital phone
  -> Plivo DID (India local number)
  -> Plivo webhook -> POST /api/v1/call/inbound/{slug}
  -> FastAPI returns PCML (SIP forward instruction)
  -> LiveKit SIP Inbound Trunk
  -> Room created: "{slug}-call-{uuid}"
  -> Dispatch Rule -> agent worker joins
  -> Arya answers in Malayalam
```

### Without Plivo (browser/web testing)

Telephony code fails gracefully when Plivo keys are absent. The agent works via LiveKit Playground. All telephony functions return `False`/empty string when not configured — no errors thrown.

### SIP setup (one-time, post-deploy)

The Telephony tab in the admin dashboard shows exactly which env vars are missing and has a **Run SIP Setup** button that creates LiveKit trunks automatically once all keys are present.

### Exotel — SIP or WebSocket streaming

Exotel works as a carrier in two modes, selected by `EXOTEL_TRANSPORT`:

- **`sip`** (default) — Exotel's webhook returns ExoML that SIP-forwards the call
  to LiveKit, same as the Plivo flow above.
- **`websocket`** — the webhook returns a **Voicebot applet** that opens a
  bidirectional WebSocket to `/ws/exotel/stream/<token>/<slug>`. Exotel streams
  the caller's audio as **raw/slin 16-bit 8 kHz mono PCM (little-endian, base64)**
  and `ExotelLiveKitBridge` publishes it into a LiveKit room, then streams the
  agent's reply back in the same format (frames are multiples of 320 bytes,
  3200–100000 bytes each). The existing `arya` agent runs unchanged.

```
Patient -> ExoPhone -> Exotel webhook -> POST /api/v1/call/inbound/exotel/<token>/<slug>
  -> Voicebot applet (EXOTEL_TRANSPORT=websocket)
  -> Exotel opens WS -> /ws/exotel/stream/<token>/<slug>
  -> ExotelLiveKitBridge joins room "{slug}-call-{uuid}" (dispatches the agent)
  -> caller audio <-> LiveKit room <-> agent
```

**Outbound over WebSocket:** set `EXOTEL_VOICEBOT_APP_ID` to an Exotel App whose
Voicebot applet points at the WS URL, then call `dial_outbound(..., carrier="exotel_ws")`.
The room is pre-created (with context + agent dispatch) and its name is passed to
Exotel via `CustomField`, which the bridge reads from the `start` event's
`custom_parameters` to join the right room.

Audio format reference: [Exotel AgentStream developer guide](https://developer.exotel.com/docs/agentstream/developer-guide).

---

## Cost Analysis

> Prices verified June 2026 against official provider documentation. Always re-verify before production budget commitments.

### Current stack — per call minute (verified)

| Component | Cost/min | Source |
|-----------|---------|--------|
| LiveKit agent session | ₹0.83 ($0.010) | livekit.io/pricing |
| LiveKit SIP minutes | ₹0.25 ($0.003) | livekit.io/pricing |
| Plivo India inbound DID | ₹0.33 ($0.0040) | plivo.com/voice/pricing/in |
| Sarvam STT (Saaras v3) | ₹0.50 | docs.sarvam.ai/pricing |
| Sarvam TTS (Bulbul v3) | ₹0.24 (~800 chars/min) | docs.sarvam.ai/pricing |
| Groq llama-3.3-70b | ₹0.04 (~2K tokens/call) | groq.com/pricing |
| **Total** | **~₹2.19/min** | |

**The ₹2/min target is achievable with the current stack.**

Switching to Groq llama-3.1-8b reduces LLM cost to ₹0.003/min (saving ₹0.04/min — negligible). The real cost driver is **LiveKit agent minutes** at ₹0.83/min, not the carrier or STT.

### Per-call economics (3-minute average)

| Metric | Value |
|--------|-------|
| Cost per call | ~₹6.60 |
| Suggested hospital charge | ₹8–12 per call |
| 50 calls/day (hospital) | ₹3,300 cost / ₹4,000–6,000 revenue |
| Front-desk staff cost | ₹8,000–15,000/month (1 person) |
| **Breakeven vs headcount** | **~150 calls/month** |

### Can we hit ₹2/min flat?

**Yes, with one trade-off.**

| Stack | Cost/min | Trade-off |
|-------|---------|-----------|
| Current (managed) | ~₹2.19 | No ops burden |
| Replace Groq 70B with 8B | ~₹2.15 | Slightly weaker complex reasoning |
| Replace LiveKit with direct SIP + custom agent | ~₹1.17 | 2–4 weeks engineering work, no cloud SLA |
| Self-hosted LiveKit + Sarvam + Plivo | ~₹1.40 | GPU/VM ops, no cloud SLA |
| Self-hosted Whisper + IndicTTS + Plivo (at 50K+ min/mo) | ~₹0.58 | Full DevOps, Malayalam quality risk |

### HIPAA compliance note

If hospitals require HIPAA-compliant processing (patient data in voice transcripts), LiveKit's **Scale plan ($500/month)** is the minimum tier that qualifies. This significantly changes unit economics at low volume (< 500 calls/month). Below that threshold, use LiveKit's standard plan with a Business Associate Agreement from your database provider (Supabase) and sign a BAA with each vendor.

### What to tell the founders

> "We're at **₹2.19/minute today** on fully-managed infrastructure — essentially at target already.
>
> Our cost is 3–5x cheaper than a single front-desk staff member. A hospital fielding 100 calls/day (avg 3 min each) pays us ₹6,600/day in infrastructure cost. They should be paying ₹9,000–15,000/day in wages for that coverage. Our margin is clear.
>
> The ₹2/min number is now a marketing headline, not a stretch goal. To go sub-₹1.50/min at scale: replace LiveKit managed with self-hosted when monthly volume exceeds 30,000 minutes.
>
> Do not drop Sarvam STT/TTS for a 50-paise saving. There is no other production-quality Malayalam voice API. Our product is only as good as the voice quality in the caller's language."

### Provider comparison (alternatives evaluated)

| Component | Current | Better for scale | Do NOT use |
|-----------|---------|-----------------|-----------|
| STT | Sarvam Saaras v3 | Self-hosted IndicWhisper at 50K+ min | Deepgram (no Malayalam) |
| TTS | Sarvam Bulbul v3 | Self-hosted IndicTTS at 50K+ min | Deepgram Aura (no Malayalam) |
| LLM | Groq llama-3.3-70b | Groq llama-3.1-8b (save ₹0.04/min) | Azure OpenAI (8x more expensive) |
| Carrier | Plivo | Telnyx SIP for outbound | Twilio (2x DID cost, higher per-min) |
| Infra | LiveKit Cloud | Self-hosted LiveKit at 30K+ min/mo | AWS Connect (complex, platform fee) |

---

## Monitoring & Observability

### Health check endpoint

```bash
curl https://your-service.onrender.com/api/v1/health
# -> {"status":"healthy","env":"production","livekit_configured":true,"plivo_configured":true}
```

### Prometheus metrics

```
GET /metrics
```

Compatible with Grafana Cloud, Datadog, and any Prometheus scraper. Available out of the box — no configuration needed.

### Structured JSON logs

All logs emit structured JSON via structlog. Key events to set alerts on:

| Event key | Severity | Action |
|-----------|---------|--------|
| `db_connection_failed` | Critical | Page on-call |
| `db_connection_timeout` | Critical | Check Supabase project paused? |
| `scheduler_start_failed` | Error | Restart web service |
| `livekit_not_installed_tools_unavailable` | Error | Check worker service pip install |
| `post-call cleanup error` | Warning | Review call log gaps |

### Call logs

Every call writes to the `call_logs` table (hospital_id, call_id, caller, turns, outcome). Visible in the admin dashboard under **Overview → Recent Calls**.

---

## Scaling Playbook

### Concurrent calls

One LiveKit agent worker handles multiple concurrent rooms. Scale the `arteq-livekit-agent` worker horizontally:

| Concurrent calls | Render instances | Monthly cost |
|-----------------|-----------------|-------------|
| 0–20 | 1 worker (Starter) | ~$7 |
| 20–100 | 3–5 workers | ~$21–35 |
| 100+ | Standard plan + autoscale | varies |

### Database connections

Default pool size: 10 connections per web process. Supabase free tier allows 25 connections. For 2+ web instances, either:
- Enable Supabase PgBouncer (connection pooling), or
- Upgrade to Supabase Pro

### Adding hospitals (no code changes needed)

Each new hospital = one row in `hospitals` + departments/doctors. No code changes, no redeployment. All routing is data-driven via the `slug`.

---

## Security Checklist

- [ ] `DASHBOARD_ADMIN_PASSWORD` is at least 12 characters and not `admin`
- [ ] `DASHBOARD_JWT_SECRET` is a cryptographically random hex string (32 bytes)
- [ ] `INTERNAL_API_KEY` is set and rotated quarterly
- [ ] `DATABASE_URL` uses SSL (`?sslmode=require`) — Supabase enforces this by default
- [ ] No API keys in code, git history, or `render.yaml`
- [ ] Plivo webhook URL uses HTTPS (Render provides this automatically)
- [ ] CORS `allow_origins` tightened for production (default is `["*"]` — restrict to your domain)
- [ ] Render preview environments disabled (prevents env var leaks to PR deployments)
- [ ] Supabase Row-Level Security enabled if exposing the DB to frontend clients directly

---

## Troubleshooting

### "Hospital not found" during calls

1. Check hospital `slug` matches the room name prefix: `SELECT slug FROM hospitals WHERE id='...'`
2. Ensure `hospitals.slug` is not NULL (run `004_hospital_tier.sql` if you see NULLs)
3. Verify the Plivo webhook URL matches: `/api/v1/call/inbound/{slug}`

### Agent doesn't join the room

1. Check `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` are set on the **worker** service
2. Worker logs: look for `livekit_not_installed_tools_unavailable`
3. Verify the SIP dispatch rule in LiveKit Cloud points to the correct room pattern

### STT returns empty transcripts

1. Verify `SARVAM_API_KEY` is valid — test directly at the Sarvam AI playground
2. Check VAD is triggering: look for `user_speech_finished` events in agent logs
3. Try `language="ml-IN"` explicitly if auto-detect is unreliable for your test audio

### Appointments not saving

1. Check `appointments_status_check` constraint includes `'requested'`
   — if not, re-run `002_appointments_callbacks.sql`
2. Verify the doctor ID exists in the `doctors` table before booking

### Database timeout on startup

1. `DATABASE_URL` format must be: `postgresql://user:pass@host:5432/dbname?sslmode=require`
2. Supabase free tier pauses after 7 days of inactivity — unpause in the Supabase dashboard
3. Ensure web service and DB are in compatible regions (Singapore → ap-southeast)

### Outbound scheduler not calling

1. `CONFIRMATIONS_ENABLED=true` must be set on the **web** service (not the worker)
2. Look for `scheduler_start_failed` in web service logs
3. Verify appointments exist with `status='confirmed'` and `slot_time` in the correct date range

---

## Project Structure

```
.
├── livekit_agent.py          LiveKit agent worker (Render worker service)
├── Makefile                  dev / test / lint / token shortcuts
├── render.yaml               Render Blueprint (web + worker services)
├── requirements.txt          Python dependencies
├── .env.example              All env vars with documentation
│
├── src/
│   ├── main.py               FastAPI entry point (webhooks, health, token endpoint)
│   ├── config/
│   │   └── settings.py       All env-var config (pydantic-settings, with validation)
│   ├── db/
│   │   └── queries.py        asyncpg queries, HospitalContext dataclass
│   ├── ai/
│   │   └── groq_brain.py     System prompt builder, greeting text helper
│   ├── telephony/
│   │   └── livekit_tools.py  LLM function tools (book/cancel/callback/emergency)
│   │                         ALL_TOOLS (hospital) and CLINIC_TOOLS (clinic tier)
│   ├── services/
│   │   ├── livekit_sip.py    SIP trunk provisioning (inbound + outbound)
│   │   ├── outbound_calls.py Reminder / confirmation / callback / followup calls
│   │   ├── scheduler.py      Background loops for proactive outbound calls
│   │   ├── sms_service.py    Plivo SMS (confirmations, location links)
│   │   ├── staff_alert.py    SMS to duty manager on key events
│   │   └── plivo_provisioning.py  Buy + configure Plivo DID
│   ├── cache/
│   │   └── store.py          MemoryCache with TTL (falls back from Redis gracefully)
│   └── observability/
│       ├── logger.py         Structured JSON logging (structlog)
│       └── metrics.py        Prometheus metrics counter/gauge
│
├── dashboard/
│   ├── routes/
│   │   ├── admin_api.py      Full CRUD REST API (hospitals, doctors, appointments, telephony)
│   │   └── auth.py           JWT auth endpoints (/api/v1/auth/login, /api/v1/auth/me)
│   └── templates/
│       └── index.html        Alpine.js SPA (no build step required)
│
├── migrations/
│   └── versions/
│       ├── 001_schema.sql              Full schema + seed demo hospital
│       ├── 002_appointments_callbacks.sql
│       ├── 003_plivo_multitenant.sql
│       └── 004_hospital_tier.sql       clinic | hospital tier support
│
└── tests/
    └── test_smoke.py         9 smoke tests (zero live API calls)
```

### Day-of-week convention

DB and all code use **0 = Sunday, 1 = Monday ... 6 = Saturday** to match `EXTRACT(DOW ...)` in PostgreSQL. This is different from Python's `datetime.weekday()` (0 = Monday). The conversion happens in `queries.py`:

```python
dow = (now.weekday() + 1) % 7   # Python Mon(0) -> DB Mon(1)
```

---

## Contributing

1. Branch off `main`: `git checkout -b feature/your-feature`
2. `make test` before pushing — all smoke tests must pass
3. `make lint` to auto-fix style issues
4. API keys must never appear in code or commits — `.env` only
