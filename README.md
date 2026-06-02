# Arteq Hospital Voice Agent

A production-grade Malayalam AI voice receptionist for Kerala hospitals.

**Stack:** LiveKit (WebRTC/SIP) → Sarvam STT + TTS → Groq LLaMA 70B → PostgreSQL (Supabase)  
**Languages:** Malayalam, Hindi, Tamil, Kannada, Telugu, English, Manglish (auto-detected)

---

## Local Development Setup

### 1. Prerequisites

| Service | Purpose | Free tier |
|---------|---------|-----------|
| [LiveKit Cloud](https://cloud.livekit.io) | WebRTC rooms + SIP | Yes |
| [Sarvam AI](https://app.sarvam.ai) | STT (Saaras v3) + TTS (Bulbul v3) | Trial credits |
| [Groq](https://console.groq.com) | LLaMA 70B LLM | Free tier |
| [Supabase](https://supabase.com) | PostgreSQL database | Free tier |

Plivo (phone calls) is only needed for production telephony. For local browser testing you only need the four services above.

### 2. Clone and install

```bash
git clone <your-repo-url>
cd Arteq-AI-Call-Assistant-

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
#   SARVAM_API_KEY
#   GROQ_API_KEY
#   DATABASE_URL  (from Supabase → Settings → Database → Connection string)
```

### 4. Set up the database

Run the schema SQL against your Supabase project (or any PostgreSQL instance):

- Open Supabase → **SQL Editor**
- Paste and run `migrations/versions/001_schema.sql`
- Run `migrations/versions/002_appointments_callbacks.sql`
- Run `migrations/versions/003_plivo_multitenant.sql`

This creates all tables and seeds a demo hospital (ID `00000000-0000-0000-0000-000000000001`, slug `demo`).

### 5. Run the app locally

Open **two terminals**:

**Terminal 1 — FastAPI web server:**
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — LiveKit agent worker:**
```bash
python livekit_agent.py dev
```

The `dev` mode connects the agent to LiveKit Cloud and auto-joins any room.

### 6. Talk to the agent in your browser

1. Get a room token:
   ```
   GET http://localhost:8000/api/v1/livekit/token?slug=demo&participant=me
   ```
   Returns `{"token": "...", "room": "demo", "url": "wss://..."}`

2. Open the [LiveKit Playground](https://meet.livekit.io), paste the token and URL, and join.

3. Talk! The agent responds in Malayalam by default and auto-detects your language.

### 7. Admin Dashboard

```
http://localhost:8000/admin/
```
Login password is `DASHBOARD_ADMIN_PASSWORD` from `.env` (default: `admin` for dev only).

---

## Production Deployment (Render)

1. Push to your branch — `render.yaml` defines both services automatically
2. In Render: **New → Blueprint** → connect repo → select `render.yaml`
3. Set all `sync: false` secrets in the Render dashboard
4. After first deploy, provision SIP trunks:
   ```
   POST https://your-service.onrender.com/admin/sip/setup
   Header: x-api-key: <INTERNAL_API_KEY>
   ```
5. Copy `livekit_sip_outbound_trunk_id` from response → set as `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` env var
6. Point your Plivo DID webhook to `https://your-service.onrender.com/api/v1/call/inbound/demo`

---

## Project Structure

```
livekit_agent.py          Agent worker (separate Render worker service)
src/
  main.py                 FastAPI app (webhooks, health, token endpoint)
  config/settings.py      All env-var configuration
  db/queries.py           asyncpg queries (no ORM)
  services/
    livekit_sip.py        LiveKit SIP trunking (inbound + outbound)
    outbound_calls.py     Reminder / confirmation / callback / followup calls
    scheduler.py          Background loops for proactive outbound calls
    sms_service.py        Plivo SMS (confirmations, location links)
    staff_alert.py        SMS alerts to duty manager
  telephony/
    livekit_tools.py      LLM function tools (book/cancel/callback/emergency)
  ai/groq_brain.py        System prompt builder + Sarvam-M fallback brain
dashboard/
  routes/admin_api.py     Admin REST API + single-admin JWT auth
migrations/
  versions/001_schema.sql Complete schema — run first on fresh DB
  versions/002_*.sql      Extends appointments + adds callbacks/feedback
  versions/003_*.sql      Multi-tenant slug + Plivo number columns
```

---

## Cost per call-minute

| Component | Cost |
|-----------|------|
| LiveKit agent | ~$0.01/min |
| Sarvam STT (Saaras v3) | ~₹0.50/min |
| Sarvam TTS (Bulbul v3) | ~₹0.30/min |
| Groq LLaMA 70B | ~$0.003/min |
| Plivo India inbound DID | ₹2.80/min |
| **Total** | **~₹5–6/min** |
