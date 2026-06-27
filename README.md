# Arteq — Multilingual AI Voice Receptionist for Hospitals

> Production-grade, multi-tenant AI phone receptionist ("Arya") for Indian
> hospitals and clinics. One self-hosted VPS serves many hospitals; routing is
> data-driven by hospital slug. The goal is to **replace the IVR menu** with a
> natural, real-time conversation — no "press 1 for…", no scripts.

**Pipeline:** Vobiz SIP → self-hosted LiveKit (WebRTC/SIP) → Sarvam Saarika STT →
Google Gemini → Sarvam Bulbul v3 TTS → caller.
**Data:** Supabase PostgreSQL (over a socat IPv4→IPv6 tunnel).
**Languages:** Malayalam-first; English, Hindi, Tamil, Kannada, Telugu — detected
per caller and locked for the call.

---

## Table of Contents
1. [Architecture](#1-architecture)
2. [Repository layout](#2-repository-layout)
3. [Environments & branches](#3-environments--branches)
4. [Deployment](#4-deployment)
5. [Environment variables](#5-environment-variables)
6. [Database & migrations](#6-database--migrations)
7. [The AI agent — behaviour](#7-the-ai-agent--behaviour)
8. [Admin dashboard](#8-admin-dashboard)
9. [Onboarding a new hospital](#9-onboarding-a-new-hospital)
10. [Telephony (Vobiz + LiveKit SIP)](#10-telephony-vobiz--livekit-sip)
11. [Call recordings](#11-call-recordings)
12. [Outbound calls, reminders & callbacks](#12-outbound-calls-reminders--callbacks)
13. [Patient messaging (WhatsApp / SMS)](#13-patient-messaging-whatsapp--sms)
14. [Operations & troubleshooting](#14-operations--troubleshooting)
15. [Security](#15-security)

---

## 1. Architecture

```
Caller dials the hospital's Vobiz DID
  -> Vobiz SIP trunk -> LiveKit SIP (inbound)
  -> room "{slug}-call-{uuid}"            (slug is the tenant routing key)
  -> LiveKit Agent worker (livekit_agent.py) is dispatched into the room
        - DTLN noise suppression (in-process, self-hosted)
        - Silero VAD (turn detection)
        - Sarvam Saarika v2.5 STT  (speech -> text, pinned ml-IN)
        - Google Gemini (gemini-2.5-flash via the OpenAI-compatible plugin)
        - Sarvam Bulbul v3 TTS    (text -> speech, "priya" voice)
  -> answers; on booking it writes to PostgreSQL and the dashboard updates live
```

**Containers** (`docker-compose.selfhost.yml`):

| Service | Role |
|---------|------|
| `nginx` | reverse proxy: `/` -> frontend, `/api/v1`,`/ws`,`/rtc`,`/admin/ws` -> app |
| `app` | FastAPI backend (Uvicorn, 2 workers) — dashboard API, schedulers, SIP setup |
| `agent` | the LiveKit voice agent worker ("arya") — one worker, all calls/tenants |
| `frontend` | Next.js admin dashboard (standalone build) |
| `livekit` | self-hosted LiveKit server (WebRTC + SIP) |
| `livekit-sip` | bridges Vobiz SIP calls into LiveKit rooms |
| `egress` | records calls to `/recordings/<call_id>.ogg` |
| `redis` | live-call event bus (agent -> dashboard WebSocket) + leader election |
| `postgres` | bundled (unused in prod — Supabase is the live DB) |

**Database:** Supabase Postgres, reached over a host `socat` IPv4->IPv6 tunnel
(`socat-supabase.service`) because the VPS is IPv4-only and Supabase is IPv6.

**Multi-tenancy:** every call's room is `"{slug}-call-{uuid}"`. The agent splits
the slug, looks up the hospital row, and loads that tenant's doctors, departments,
FAQs, hours, holidays, greeting, language, plan and recordings. No per-tenant
process, port or container.

---

## 2. Repository layout

```
livekit_agent.py            # the voice agent: pipeline, prompt, tools, watchdogs
src/
  ai/groq_brain.py          # system-prompt builder + hospital summary + greeting
  telephony/livekit_tools.py# agent tools: book, availability, transfer, emergency…
  db/queries.py             # HospitalContext load, slots, call_log, appointments
  services/
    vobiz_sip.py            # inbound/outbound SIP trunk + dispatch-rule setup
    scheduler.py            # leader-elected background loops (reminders, queue…)
    appointment_workflow.py # 3x retry calling, calling-hours, reminders
    staff_alert.py          # duty-manager / emergency SMS
  config/settings.py        # env + production validators
dashboard/routes/admin_api.py   # all /admin/* REST + WS endpoints
additions/                  # live-call WS bus, usage/cost, monitoring
frontend/                   # Next.js dashboard (app router, src/app/(app)/*)
migrations/versions/*.sql   # idempotent SQL migrations (run on app boot)
scripts/
  add_tenant.sh / .py       # provision a new hospital (one command)
  update.sh                 # pull + rebuild + restart (deploy)
  test_outbound.sh / .py    # place a test outbound call
docker-compose.selfhost.yml # the production stack
ONBOARDING.md               # new-hospital runbook
```

---

## 3. Environments & branches

| Branch | Role |
|--------|------|
| `main` | **Production** (default). The VPS tracks this; `update.sh` deploys it. |
| `staging` | pre-production testing |
| `development` | active development |

Flow: `development` -> `staging` -> `main`. Deploy a release by merging to `main`
and running `./scripts/update.sh` on the VPS.

Runtime mode is `ENV=production` in `.env`: production secret validators enforce a
>=12-char dashboard admin password and a non-weak JWT secret, CORS is locked to the
host, Uvicorn runs without `--reload`, and the frontend is a production build.

---

## 4. Deployment

The whole stack lives at `/root/arteq` on the VPS.

```bash
# First boot
cd /root/arteq
cp .env.example .env          # then fill in the keys (section 5)
docker compose -f docker-compose.selfhost.yml up -d --build

# Deploy an update (pull main, rebuild images, restart)
./scripts/update.sh
```

> **Important:** always **rebuild** the `agent` image after agent-side changes
> (`docker compose -f docker-compose.selfhost.yml build agent`). The running agent
> uses the built image, not the source on disk — a bare restart will not pick up
> edits. `update.sh` rebuilds `app`, `agent` and `frontend`.

Migrations run automatically on app boot (idempotent). Health check:
`curl -s http://localhost/api/v1/health` -> `200`.

---

## 5. Environment variables

`/root/arteq/.env` (gitignored). Key groups:

| Group | Keys |
|-------|------|
| Runtime | `ENV=production`, `NODE_IP=<VPS public IP>` |
| Database | `DATABASE_URL` (Supabase, via socat), `DB_SSL=require` |
| LiveKit | `LIVEKIT_URL=ws://livekit:7880`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_DISPATCH_NAME` |
| STT/TTS | `SARVAM_API_KEY`, `SARVAM_STT_MODEL=saarika:v2.5`, `SARVAM_STT_LANGUAGE` (blank -> per-tenant `agent_language`), `TTS_PACE` |
| LLM | `GOOGLE_API_KEY`, `GOOGLE_MODEL=gemini-2.5-flash` |
| Voice tuning | `VAD_ACTIVATION_THRESHOLD`, `VAD_MIN_SILENCE`, `VAD_MIN_SPEECH`, `DTLN_STRENGTH` (0 = off), `GREETING_COOLDOWN_S`, `INACTIVITY_PROMPT_S`, `INACTIVITY_HANGUP_S`, `MAX_CALL_DURATION_S` |
| Dashboard auth | `DASHBOARD_ADMIN_PASSWORD` (>=12 in prod), `DASHBOARD_JWT_SECRET`, `SUPERADMIN_EMAIL`, `NEXTAUTH_SECRET`, `NEXTAUTH_URL`, `CORS_ORIGINS` |
| Vobiz SIP | `VOBIZ_PHONE_NUMBER` (DID, +E.164), `VOBIZ_SIP_USERNAME`, `VOBIZ_SIP_PASSWORD`, `VOBIZ_SIP_OUTBOUND_DOMAIN`, `LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID` |
| Messaging | `WHATSAPP_ENABLED`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`; or `SMS_PROVIDER` + provider keys |
| Outbound | `OUTBOUND_QUEUE_INTERVAL_SECONDS`, `STAFF_ALERT_PHONE` |

A new tenant's `agent_language` / `agent_name` / `greeting` are stored **per
hospital in the DB** (set from the dashboard), not in `.env`.

---

## 6. Database & migrations

Migrations are plain SQL in `migrations/versions/*.sql`, applied on every app boot
(idempotent — `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`).

Core tables: `hospitals`, `users`, `user_tenants`, `departments`, `doctors`,
`schedules`, `appointments`, `appointment_events`, `call_logs`, `faqs`,
`emergency_contacts`, `billing_info`, `callbacks`, `outbound_call_queue`,
`doctor_patients`, `hospital_holidays`.

Notable columns added this cycle:

| Table | Columns | Purpose |
|-------|---------|---------|
| `hospitals` | `plan` (trial\|full), `greeting`, `staff_alert_phone`, `agent_name`, `agent_language` | per-tenant agent config |
| `departments` | `timings` | what the agent tells callers per department |
| `appointments` | `patient_age`, `patient_age_unit` (years\|months\|weeks\|days), `patient_gender` | any-age patients |
| `call_logs` | `recording_url`, `patient_name/age/age_unit/gender`, `direction` | recording link + call-captured details |
| `hospital_holidays` | `holiday_date`, `reason`, `closed`, `open_time`, `close_time` | closures / special hours |

**`plan`** gates inbound AI answering: `full` = inbound + outbound + dashboard;
`trial` = outbound reminders + dashboard only (inbound is not AI-answered).

---

## 7. The AI agent — behaviour

The agent is a single LiveKit worker handling every call. Behaviour (system prompt
in `groq_brain.py` + tools in `livekit_tools.py`):

- **Open-world, not IVR.** Talks like a real receptionist — no menus, no fixed
  question order, follows the caller. The only hard boundary is the **topic**:
  this hospital + general medical/health. Genuinely off-topic requests are
  declined warmly; an *unclear/garbled* word is **not** declined — the agent guesses
  the closest department/doctor/service and asks "did you mean X?".
- **Language.** Detects the caller's language from their first message and locks it
  for the call; switches only on an explicit request. Native, colloquial Malayalam.
- **Malayalam time.** Clock times are spoken in Malayalam words with part-of-day
  (e.g. "രാവിലെ ഒമ്പത് മണി മുതൽ … വരെ"), not digits.
- **Symptom routing.** A described symptom is mapped to the right department
  (chest pain -> Cardiology, fever -> General Medicine, …); urgent symptoms -> Emergency.
- **Booking flow.** Natural checklist (department -> who -> name -> age -> gender ->
  offer a slot). Lists **doctor names first**; a doctor's time slots are given only
  after the caller picks that doctor. Confirms the full details **once** at the end.
  Age accepts **any unit** (years default; months/weeks/days for infants). Doctor
  match works on first **or** last name, and asks "which one?" when a name is shared.
- **Availability.** Slots are derived from each doctor's `schedules` minus booked
  appointments, with past times filtered out for today; if today is full it
  automatically searches the next working days.
- **Holidays.** On a closed date the agent says the hospital is closed (or gives
  special hours) instead of offering appointments.
- **Silence handling.** A watchdog says "are you there?" only after the caller has
  been silent for `INACTIVITY_PROMPT_S` **while the agent is listening** (never
  during/right after its own reply), re-prompts on continued silence, and hangs up
  after `INACTIVITY_HANGUP_S`. A `MAX_CALL_DURATION_S` cap protects cost.
- **Voicemail.** An outbound call answered by a carrier/iPhone voicemail is detected
  and ended (counts under the 3x retry rule) — never talks to a machine.
- **Noise.** DTLN in-process suppression (`DTLN_STRENGTH`, light by default) — works
  self-hosted, unlike LiveKit Cloud Krisp.
- **Tools:** `book_appointment`, `check_availability`,
  `check_department_availability`, `get_doctor_schedule`, `remember_patient`,
  `reschedule_appointment`, `cancel_appointment`, `request_callback`,
  `send_location_sms`, `transfer_to_department`, `alert_emergency`, `end_call`.

---

## 8. Admin dashboard

Next.js app served at `http://<vps>/` (login at `/login`). Two roles:
**super admin** (manages all tenants) and **hospital admin** (one tenant).

Hospital-admin pages: Overview, Calls (+ recordings), Call QA, Analytics, Live
(real-time active calls over WebSocket), Appointments, Callbacks (re-dial / done /
cancel), Usage & Cost, Patients, Bookings & Tokens, WhatsApp, Settings (greeting,
language, hours, staff-alert phone), Departments, Doctors (+ Schedules), FAQs,
Holidays, Billing, Emergency, Knowledge, Telephony, Setup, HIS, My Account.

Super-admin pages: Hospitals (plan/tier toggle), Onboard hospital, Tenants, Users &
Roles, Usage (all). **Recordings are hidden from super admin** (patient privacy) —
only the owning hospital admin can play them.

Auth: NextAuth credentials -> backend `/admin/login` (returns a JWT carrying role +
tenants). `api.ts` proxies `/admin/api/*` -> backend `/admin/*`.

---

## 9. Onboarding a new hospital

See **`ONBOARDING.md`** for the full runbook. In short, on the VPS:

```bash
./scripts/add_tenant.sh \
  --name "City Clinic" --slug city-clinic \
  --admin-user cityclinic --admin-pass 'Strong@Pass1' \
  --did +917900000000 --plan full --language ml-IN
```

Creates the hospital row, a tenant-scoped dashboard admin login, and (with `--did`)
the LiveKit inbound SIP trunk + dispatch rule. The admin then configures
departments, doctors + schedules, FAQs, holidays and Settings via the dashboard —
no code or DB work. Idempotent on `--slug`.

---

## 10. Telephony (Vobiz + LiveKit SIP)

- **Inbound:** a Vobiz DID routes to LiveKit SIP, which creates the room and
  dispatches the agent. The inbound trunk registers **every DID format**
  (`+91…`, `91…`, bare 10-digit, `0…`) so callers reach the agent with or without
  the country code. The caller's number is parsed from the SIP identity and stored.
- **Outbound:** uses the Vobiz **per-trunk** SIP domain + SIP credentials (not the
  REST key), `wait_until_answered=True`.
- Setup is done by `vobiz_sip.py` (`setup_hospital_inbound_vobiz`,
  `setup_vobiz_outbound_trunk`) and the `/admin/sip/vobiz/setup` endpoint;
  `add_tenant.sh --did` wires inbound automatically per hospital.

Firewall (UDP unless noted): 80/443 TCP, 7880/7881 TCP (LiveKit), 5060 UDP (SIP),
WebRTC + RTP media ranges.

---

## 11. Call recordings

LiveKit Egress records each call to `/recordings/<call_id>.ogg`. A `call_log` row
is written **at call start** (stub) and upserted at end, so a recording is always
linkable even if the call crashes. The dashboard serves the file via
`GET /admin/.../calls/{call_id}/recording`, scoped to the owning hospital and
**denied to super admins**. Downloads are named
`<patient>_<age><unit>_<gender>_<date>_Dr-<doctor>.ogg`.

The recordings Docker volume must be writable by the egress user (uid 1001);
`update.sh` self-heals the permission on deploy.

---

## 12. Outbound calls, reminders & callbacks

Background loops in `scheduler.py` run on a single **leader-elected** worker
(Postgres advisory lock) so two Uvicorn workers never double-dial:
confirmation, reminder (~2 h before), doctor-availability, follow-up, and the
`outbound_call_queue` dispatcher. `appointment_workflow.py` enforces calling hours
(IST) and a 3x retry rule; an exhausted appointment is marked `missed`.
Callbacks captured on a call appear in the dashboard and can be re-dialled.

---

## 13. Patient messaging (WhatsApp / SMS)

`whatsapp_service.py` sends confirmations/reminders via the Meta WhatsApp Cloud API
(approved Utility templates); if WhatsApp is unavailable it falls back to SMS
(`sms_service.py`, provider-gated). **Not configured by default** — set
`WHATSAPP_ENABLED` + token, or `SMS_PROVIDER` + keys. Until then the agent does not
claim a message was sent.

---

## 14. Operations & troubleshooting

```bash
# Logs
docker compose -f docker-compose.selfhost.yml logs agent --since 10m
docker compose -f docker-compose.selfhost.yml logs app  --since 10m
docker compose -f docker-compose.selfhost.yml logs livekit-sip --since 10m   # call routing

# Place a test outbound call
./scripts/test_outbound.sh +9190XXXXXXXX
```

| Symptom | Likely cause |
|---------|--------------|
| Silent call / 402 in logs | Sarvam STT/TTS out of credits — top up at dashboard.sarvam.ai |
| Agent edits not taking effect | rebuild the `agent` image (don't just restart) |
| Recording "not available" | call_log row missing — fixed by the stub-at-start; older orphans can be backfilled |
| Inbound call never reaches the server | not in `livekit-sip` logs -> Vobiz routing / DID format / wrong number dialled |
| Garbled transcription | noisy line; STT is Saarika ml-IN; lower/raise `DTLN_STRENGTH` |
| Caller shows "unknown" | older call before SIP-identity parsing |

---

## 15. Security

- `ENV=production` enforces a strong dashboard password + non-weak JWT secret and
  locks CORS to the host.
- Recordings + transcripts are tenant-scoped and hidden from super admins.
- Passwords are bcrypt-hashed; tenant admins cannot change plan/tier/slug/active
  (super-admin only).
- The SIP inbound trunk only accepts Vobiz's IP ranges.
- `.env` is gitignored; rotate `DASHBOARD_JWT_SECRET`, `LIVEKIT_API_SECRET` and SIP
  credentials on any suspected exposure.
