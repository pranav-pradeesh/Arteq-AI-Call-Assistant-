# Arteq Hospital Voice Agent

> **Production-grade multilingual AI voice receptionist for Indian hospitals and clinics.**
>
> One codebase. Multi-tenant. Deploy one VPS for dozens of hospitals — routing is purely data-driven.

**Stack:** LiveKit (WebRTC/SIP) → Sarvam AI STT + TTS → Google Gemini → PostgreSQL  
**Telephony:** Vobiz SIP trunking (Indian DID, no SMS — WhatsApp for patient messages)  
**Languages:** Malayalam, Hindi, Tamil, Kannada, Telugu, Bengali, Gujarati, Marathi, Punjabi, Odia, English — auto-detected per caller, no configuration needed

---

## Table of Contents

1. [Architecture](#architecture)
2. [Quick Start](#quick-start)
3. [Environment Variables](#environment-variables)
4. [Database & Migrations](#database--migrations)
5. [Admin Dashboard](#admin-dashboard)
6. [Multi-Hospital VPS Setup](#multi-hospital-vps-setup)
7. [Telephony Setup (Vobiz)](#telephony-setup-vobiz)
8. [WhatsApp Notifications](#whatsapp-notifications)
9. [Call Recordings](#call-recordings)
10. [Cost: Under ₹2/min](#cost-under-₹2min)
11. [Security](#security)
12. [Production Deployment](#production-deployment)
13. [Project Structure](#project-structure)
14. [Troubleshooting](#troubleshooting)

---

## Architecture

```
Patient dials hospital landline
  → BSNL/MTNL call forward → Vobiz DID
  → Vobiz SIP trunk → LiveKit SIP Inbound
  → Room: "{slug}-call-{uuid}"   ← slug routes to the right hospital
  → LiveKit Agent Worker (livekit_agent.py)
        ├── Sarvam STT saarika:v2.5  (speech → text, auto language)
        ├── Google Gemini 2.0 Flash  (LLM brain, function calls)
        │     └── Sarvam sarvam-30b  (fallback LLM)
        └── Sarvam TTS bulbul:v3     (text → speech, caller's language)
              ↓
          Patient hears Arya
              ↓ (side effects)
        WhatsApp notifications via Meta Cloud API
        DB writes: appointments, callbacks, call_logs
```

Two services (can run on the same VPS or split):
- **API server** — FastAPI (webhooks, admin dashboard, health, scheduler)
- **Agent worker** — LiveKit agent (Arya, handles all concurrent call rooms)

---

## Quick Start

### Local development (browser testing, no phone needed)

```bash
# 1. Clone
git clone <your-repo-url> && cd Arteq-AI-Call-Assistant-

# 2. Setup — one command does everything
chmod +x setup.sh && ./setup.sh

# 3. Edit .env with your API keys (minimum required below)
# 4. Start
make dev          # terminal 1: FastAPI server → http://localhost:8000
make agent        # terminal 2: LiveKit agent worker

# Open http://localhost:8000/talk → press Start Call → talk to Arya
```

Or with Docker (includes Postgres + Redis):

```bash
docker compose up --build
```

### Minimum required `.env` keys for local dev

```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
SARVAM_API_KEY=...
GOOGLE_API_KEY=...
DATABASE_URL=postgresql://user:pass@host:5432/arteq
DASHBOARD_ADMIN_PASSWORD=your-strong-password
DASHBOARD_JWT_SECRET=<run: python -c "import secrets; print(secrets.token_hex(32))">
```

---

## Environment Variables

All variables are documented in `.env.example`. Key groups:

| Group | Variables | Required |
|-------|-----------|---------|
| LiveKit | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Yes |
| Sarvam AI | `SARVAM_API_KEY` | Yes |
| Google Gemini | `GOOGLE_API_KEY`, `GOOGLE_MODEL` | Yes |
| Database | `DATABASE_URL` | Yes |
| Dashboard auth | `DASHBOARD_ADMIN_PASSWORD`, `DASHBOARD_JWT_SECRET` | Yes |
| Vobiz SIP | `VOBIZ_API_KEY`, `VOBIZ_API_SECRET`, `VOBIZ_PHONE_NUMBER` | Production |
| WhatsApp | `WHATSAPP_ENABLED`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` | For patient messages |
| Recording | `VOBIZ_RECORD_CALLS=true` | Optional |

---

## Database & Migrations

Migrations run **automatically on every server startup** (idempotent — safe to re-run). No manual steps needed for Supabase or Docker Postgres.

To apply manually (Supabase SQL Editor or psql):

```bash
for f in migrations/versions/*.sql; do psql "$DATABASE_URL" -f "$f"; done
```

Schema seeds a demo hospital: slug `demo`, ID `00000000-0000-0000-0000-000000000001`.

### Adding a new hospital (no code changes, no restart)

```bash
POST /admin/hospitals/wizard
Authorization: Bearer <token>

{
  "name": "Malabar Super Speciality Hospital",
  "slug": "malabar-hospital",
  "address": "Kozhikode, Kerala",
  "phone": "+914952XXXXXX",
  "tier": "hospital",
  "departments": [
    {
      "name": "Cardiology",
      "doctors": [{"name": "Dr. Ramesh Kumar", "specialty": "Cardiologist",
        "schedules": [{"day_of_week": 1, "start_time": "09:00", "end_time": "13:00"}]}]
    }
  ]
}
```

Each hospital = one slug. The agent loads the right context automatically. No redeployment needed.

---

## Admin Dashboard

```
http://localhost:8000/admin/
```

Login with `DASHBOARD_ADMIN_PASSWORD`.

| Tab | Purpose |
|-----|---------|
| Overview | Live call stats, recent calls, WebSocket URL |
| Settings | Hospital name, hours, slug, tier |
| Departments | CRUD for OPD, ICU, Pharmacy, Lab |
| Doctors | CRUD with weekly schedule slots |
| Billing | Fee ranges per consultation type |
| Emergency | Priority-ranked emergency contacts |
| FAQs | Q&A the AI uses for caller questions |
| Appointments | View/confirm/cancel bookings |
| Callbacks | Pending callback requests |
| Calls | Call history + recordings (if enabled) |
| Telephony | SIP setup trigger + config status |

### REST API

```bash
# Auth
POST /admin/login  {"password": "..."}  → {"access_token": "..."}

# Hospitals
GET  /admin/hospitals
POST /admin/hospitals/wizard
GET  /admin/hospitals/{id}
PUT  /admin/hospitals/{id}

# Calls + recordings
GET  /admin/hospitals/{id}/calls?limit=50
GET  /admin/hospitals/{id}/calls/{call_id}
GET  /admin/hospitals/{id}/recordings?limit=50

# Telephony
POST /admin/sip/vobiz/setup
GET  /admin/telephony/status?hospital_id={id}
```

Full interactive docs: `http://localhost:8000/docs`

---

## Multi-Hospital VPS Setup

One VPS can serve any number of hospitals simultaneously. Routing is by hospital `slug` in the room name — no separate process or port per hospital.

```bash
# Start the full self-hosted stack (LiveKit + Vobiz SIP + Nginx + DB + Redis)
docker compose -f docker-compose.selfhost.yml up -d
```

What it runs:
- **Nginx** (TLS termination, reverse proxy to the app)
- **LiveKit server** (self-hosted, no LiveKit Cloud fee)
- **LiveKit SIP** (bridges Vobiz calls into LiveKit rooms)
- **App** (FastAPI, 2 Uvicorn workers)
- **Agent** (Arya, handles all rooms concurrently)
- **Postgres + Redis** (shared by all hospitals)

### Required env for self-hosting

```env
NODE_IP=<your VPS public IP>    # for WebRTC media reachability
POSTGRES_PASSWORD=<strong>
REDIS_PASSWORD=<strong>         # leave blank to disable Redis auth
```

### TLS / HTTPS

Place your certificates in the `nginx_certs` Docker volume:
- `/etc/nginx/certs/fullchain.pem`
- `/etc/nginx/certs/privkey.pem`

For Let's Encrypt:
```bash
# Run certbot on the host, then copy certs into the volume
certbot certonly --standalone -d arteq.yourdomain.com
docker cp /etc/letsencrypt/live/arteq.yourdomain.com/fullchain.pem \
  $(docker volume inspect arteq_nginx_certs -f '{{.Mountpoint}}')/
```

### Firewall ports to open

| Port | Protocol | Purpose |
|------|----------|---------|
| 80, 443 | TCP | HTTP/HTTPS (Nginx) |
| 7880 | TCP | LiveKit WebSocket (internal) |
| 7881 | TCP | LiveKit RTC TCP |
| 5060 | UDP | SIP signaling from Vobiz |
| 50000–50200 | UDP | WebRTC media |
| 10000–10100 | UDP | SIP RTP media |

---

## Telephony Setup (Vobiz)

### Call flow

```
Patient → hospital landline → BSNL/MTNL call forward → Vobiz DID
  → Vobiz SIP trunk → LiveKit SIP Inbound Trunk → room created
  → agent dispatched → Arya answers
```

### One-time setup

1. Sign up at [vobiz.ai](https://vobiz.ai) → get API key, secret, phone number
2. Set `VOBIZ_API_KEY`, `VOBIZ_API_SECRET`, `VOBIZ_PHONE_NUMBER` in `.env`
3. POST SIP trunk setup:
   ```bash
   POST /admin/sip/vobiz/setup
   Authorization: Bearer <token>
   # Returns: livekit_sip_vobiz_outbound_trunk_id
   ```
4. Copy the returned trunk ID → set `LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID` in `.env`
5. Get your SIP host from LiveKit Cloud → SIP → Inbound Trunks → set `LIVEKIT_SIP_HOST`
6. Redeploy

### Hospital landline forwarding

```
BSNL/MTNL forward: dial **21*+918047XXXXXX#  (your Vobiz DID)
Airtel:             *67*+918047XXXXXX#
```

---

## WhatsApp Notifications

Patient notifications (appointment confirmation, reminder, cancellation, token status, location) are sent via Meta WhatsApp Cloud API using pre-approved Utility templates.

**No SMS** — Vobiz is SIP-only. WhatsApp is the only patient messaging channel.

### Setup

1. Create a WhatsApp Business app at [developers.facebook.com](https://developers.facebook.com)
2. Get permanent system-user access token + phone number ID
3. Create Utility templates in Meta Business Manager (names in `.env.example`)
4. Set in `.env`:
   ```env
   WHATSAPP_ENABLED=true
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_ACCESS_TOKEN=...
   WHATSAPP_TEMPLATE_LANG=en   # or ml, hi, etc.
   ```

Template variable order is documented in `src/services/whatsapp_service.py`.

---

## Call Recordings

Vobiz provides a full call recording API (MP3/WAV, mono/stereo).

### Enable

```env
VOBIZ_RECORD_CALLS=true
VOBIZ_RECORDING_FORMAT=mp3      # mp3 or wav
VOBIZ_RECORDING_CHANNELS=mono   # mono or stereo
```

### Access recordings

- **Dashboard:** Admin → Hospital → Calls tab → click any call → play/download
- **API:** `GET /admin/hospitals/{id}/recordings`
- **Direct download:** `https://media.vobiz.ai/v1/Account/{api_key}/Recording/{id}.mp3`

Check recording storage pricing in the [Vobiz console](https://console.vobiz.ai) before enabling in production.

---

## Cost: Under ₹2/min

| Service | Purpose | Monthly est. (750 min/day) | Per minute |
|---------|---------|--------------------------|------------|
| Vobiz SIP | Telephony | ₹9,000 | ₹0.40 |
| Sarvam AI | STT + TTS | ₹10,000 | ₹0.44 |
| Google Gemini | LLM brain | ₹1,000 | ₹0.04 |
| WhatsApp | Patient messages | ₹2,500 | ₹0.11 |
| **Total** | | **₹22,500/mo** | **₹1.00/min** |

**With 30% buffer: ₹29,500/mo → ₹1.31/min — comfortably under ₹2.00/min.**

Self-hosting LiveKit (included in `docker-compose.selfhost.yml`) eliminates the LiveKit Cloud fee entirely.

---

## Security

### Login brute-force protection

Both login endpoints (`POST /admin/login`, `POST /api/v1/auth/login`) are rate-limited to **5 failed attempts per 10-minute window per IP**. Returns HTTP 429 when exceeded.

Nginx adds a second layer of rate limiting at the reverse proxy (10 req/min to auth endpoints).

### JWT

- Algorithm: HS256, signed with `DASHBOARD_JWT_SECRET`
- TTL: 720 minutes (configurable via `DASHBOARD_JWT_EXPIRE_MINUTES`)
- Generate a strong secret: `python -c "import secrets; print(secrets.token_hex(32))"`

### Production checklist

- [ ] `DASHBOARD_ADMIN_PASSWORD` ≥ 12 chars, not `admin`
- [ ] `DASHBOARD_JWT_SECRET` is a random 32-byte hex string
- [ ] `INTERNAL_API_KEY` is set and rotated quarterly
- [ ] `DATABASE_URL` uses SSL (`?sslmode=require`) — Supabase enforces this
- [ ] `CORS_ORIGINS` set to your actual domain(s), not `*`
- [ ] `ENV=production` set on the server
- [ ] TLS/HTTPS enabled (Nginx in `docker-compose.selfhost.yml`, or Render auto-TLS)
- [ ] Firewall open only on ports listed in [Firewall ports](#firewall-ports-to-open)
- [ ] No API keys in code or git history

---

## Production Deployment

### Option A: Render.com (managed, easiest)

1. Push to GitHub
2. **Render → New → Blueprint** → connect repo → select `render.yaml`
3. Set secrets in Render Dashboard → Environment for each service
4. Deploy — auto-deploys on every push
5. After first deploy: `POST /admin/sip/vobiz/setup` to register SIP trunks

### Option B: Self-hosted VPS (cheapest at scale)

```bash
# 1. Provision a VPS (Oracle Cloud Free Tier or ₹500/mo DigitalOcean 4GB)
# 2. Clone repo
git clone <repo> && cd Arteq-AI-Call-Assistant-
cp .env.example .env    # fill secrets

# 3. Run one-shot setup (migrations + SIP trunk registration)
chmod +x setup.sh && ./setup.sh

# 4. Start everything
docker compose -f docker-compose.selfhost.yml up -d

# 5. Add hospitals via wizard
curl -X POST https://yourdomain.com/admin/hospitals/wizard \
  -H "Authorization: Bearer <token>" ...
```

### Health check

```bash
curl https://your-service.onrender.com/api/v1/health
# {"status":"healthy","version":"2.1.0","livekit_configured":true,...}
```

---

## Project Structure

```
.
├── livekit_agent.py         LiveKit agent worker (Arya) — runs as separate process
├── setup.sh                 One-shot setup: migrations + SIP trunks + hospital wizard
├── Dockerfile               Single image for both API server and agent
├── docker-compose.yml       Local dev stack (Postgres + Redis + app + agent)
├── docker-compose.selfhost.yml  Production VPS stack (+ LiveKit + SIP + Nginx)
├── render.yaml              Render Blueprint (managed cloud deploy)
├── requirements.txt
├── .env.example             All env vars documented
│
├── src/
│   ├── main.py              FastAPI entry point + lifespan (migrations, scheduler)
│   ├── config/settings.py   Pydantic env config with validation
│   ├── db/queries.py        asyncpg queries + HospitalContext dataclass
│   ├── ai/                  System prompt builder
│   ├── telephony/
│   │   └── livekit_tools.py LLM function tools (book/cancel/callback/emergency)
│   ├── services/
│   │   ├── livekit_sip.py       SIP trunk provisioning
│   │   ├── outbound_calls.py    Reminder / confirmation / callback / followup dialer
│   │   ├── scheduler.py         Background scheduler loops
│   │   ├── sms_service.py       No-op base (Vobiz is SIP-only, no SMS)
│   │   ├── whatsapp_service.py  Meta WhatsApp Cloud API (patient notifications)
│   │   ├── vobiz_recording.py   Vobiz recording API (list, download, start)
│   │   └── staff_alert.py       SMS/WhatsApp to duty manager on key events
│   ├── cache/store.py           In-memory + Redis cache
│   └── observability/           Structured JSON logging + Prometheus metrics
│
├── dashboard/
│   ├── routes/
│   │   ├── admin_api.py     Full CRUD REST API (hospitals, doctors, calls, recordings)
│   │   └── auth.py          JWT auth (/api/v1/auth/login, /api/v1/auth/me)
│   └── templates/           Alpine.js SPA dashboard
│
├── additions/               Analytics, QA review, live monitoring, RBAC users
│
├── migrations/versions/     Numbered idempotent SQL migrations (auto-applied)
│   ├── 001_schema.sql       Full schema + demo hospital
│   ├── ...
│   └── 021_vobiz_config.sql Vobiz trunk columns on hospitals/tenants
│
├── deploy/
│   ├── livekit.yaml         LiveKit server config (self-host)
│   ├── livekit-sip.yaml     LiveKit SIP service config
│   └── nginx.conf           Nginx reverse proxy config (TLS, rate-limiting)
│
└── tests/
    └── test_smoke.py        Smoke tests (no live API calls)
```

---

## Troubleshooting

### Agent doesn't answer calls

1. Check `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` are set on the **agent** process
2. Agent logs: `python livekit_agent.py start` → look for `worker_registered`
3. Self-hosted LiveKit: ensure `NODE_IP` is set to the VPS public IP

### Hospital not found during calls

1. Check slug in room name matches: `SELECT slug FROM hospitals WHERE active=true`
2. Verify the Vobiz webhook / SIP dispatch rule uses the correct slug pattern

### STT returns empty transcripts

1. Verify `SARVAM_API_KEY` — test at [app.sarvam.ai](https://app.sarvam.ai/playground)
2. Check VAD triggers: look for `user_speech_finished` in agent logs
3. Use `SARVAM_STT_LANGUAGE=ml-IN` to pin language if auto-detect is unreliable

### WhatsApp messages not sending

1. `WHATSAPP_ENABLED=true` and `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_ACCESS_TOKEN` must be set
2. Templates must be approved in Meta Business Manager
3. Check the `whatsapp_failed` log events for HTTP status codes

### Recordings not appearing

1. `VOBIZ_RECORD_CALLS=true`, `VOBIZ_API_KEY` and `VOBIZ_API_SECRET` must be set
2. Check `vobiz_recording_start_failed` in logs for Vobiz API errors
3. Recordings appear in the Vobiz console at [console.vobiz.ai](https://console.vobiz.ai)

### Database timeout on startup

1. `DATABASE_URL` must be: `postgresql://user:pass@host:5432/dbname?sslmode=require`
2. Supabase free tier pauses after 7 days inactivity — unpause in the dashboard
3. Docker: ensure `DB_SSL=disable` when connecting to the local postgres container
