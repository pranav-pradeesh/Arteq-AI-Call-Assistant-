# Deploying Arteq on a Hostinger KVM VPS (self-hosted)

End-to-end runbook to take Arteq live for a real hospital on a single Hostinger
KVM VPS (KVM2 or larger, ~2 vCPU / 8 GB), running the full self-hosted stack from
`docker-compose.selfhost.yml`: app + Arya agent + self-hosted LiveKit + LiveKit
SIP + Postgres + Redis + Nginx. One VPS serves any number of hospitals — routing
is data-driven by the hospital `slug`.

> Replace `arteq.yourdomain.com` and `<VPS_IP>` throughout with your real domain
> and the VPS public IP. Run everything in the Hostinger panel **Terminal** (or
> your own SSH session as root).

To keep commands short, set an alias for the session:

```bash
alias dc="docker compose -f docker-compose.selfhost.yml"
```

## 0. Before you start — have these ready

- A **funded** `GOOGLE_API_KEY` (Gemini is the sole LLM brain — an unfunded Google
  key 429s every turn and, with no fallback, that means silence). If you don't have
  funded Google billing, use OpenRouter instead (`LLM_PROVIDER=openrouter` +
  `OPENROUTER_API_KEY`, one prepaid key, same model).
- `SARVAM_API_KEY` (powers STT/Saarika + TTS/Bulbul — still required).
- Vobiz `API_KEY` / `API_SECRET` / `PHONE_NUMBER` (the hospital's DID).
- A domain you control (for TLS).

## 1. DNS

Point an A record `arteq.yourdomain.com` → `<VPS_IP>` and wait for it to resolve.

## 2. Docker + firewall

```bash
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH
ufw allow 80,443/tcp && ufw allow 7880,7881/tcp
ufw allow 5060/udp && ufw allow 50000:50200/udp && ufw allow 10000:10100/udp
ufw --force enable
```

Then open the **same** ports in the Hostinger panel firewall (**Security →
Firewall**) — it sits in front of `ufw`, so both must allow them or inbound calls
and audio are blocked. The UDP ranges are mandatory for SIP/WebRTC media.

| Port | Proto | Purpose |
|------|-------|---------|
| 80, 443 | TCP | HTTP/HTTPS (Nginx) |
| 7880, 7881 | TCP | LiveKit WS + RTC TCP |
| 5060 | UDP | SIP signaling from Vobiz |
| 50000–50200 | UDP | WebRTC media |
| 10000–10100 | UDP | SIP RTP media |

## 3. Get the code

```bash
git clone https://github.com/pranav-pradeesh/Arteq-AI-Call-Assistant-.git
cd Arteq-AI-Call-Assistant-
cp .env.example .env
nano .env
```

## 4. Configure `.env`

`cp .env.example .env` gives the full file with safe defaults. Change **only** the
values below; leave everything else at its default.

```env
NODE_IP=<VPS_IP>
PUBLIC_BASE_URL=https://arteq.yourdomain.com
PUBLIC_WS_URL=wss://arteq.yourdomain.com

# Postgres (self-host) — password MUST match in both lines; host = postgres
POSTGRES_USER=arteq
POSTGRES_PASSWORD=<strong-db-pw>
POSTGRES_DB=arteq_hospital
DATABASE_URL=postgresql://arteq:<strong-db-pw>@postgres:5432/arteq_hospital
REDIS_URL=redis://redis:6379/0

# LLM — pick ONE working option (this is the #1 cause of a silent call)
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<FUNDED google key>
# …or, if no funded Google billing:
# LLM_PROVIDER=openrouter
# OPENROUTER_API_KEY=<key>

SARVAM_API_KEY=<key>
LIVEKIT_API_KEY=<any strong string>        # self-host: you define these
LIVEKIT_API_SECRET=<any strong string>
VOBIZ_API_KEY=<key>
VOBIZ_API_SECRET=<secret>
VOBIZ_PHONE_NUMBER=<hospital DID, +91...>
DASHBOARD_ADMIN_PASSWORD=<your super-admin password>
```

`DASHBOARD_JWT_SECRET` and `INTERNAL_API_KEY` auto-generate on first run — leave
them at their defaults.

## 5. TLS certificate

Get the cert before launching (port 80 must be free):

```bash
apt install -y certbot
certbot certonly --standalone -d arteq.yourdomain.com
```

## 6. Migrations + launch

```bash
./setup.sh --docker      # validates .env + runs DB migrations (SIP warning here is fine)
dc up -d                 # postgres, redis, livekit, sip, app, agent, nginx
sleep 30 && dc ps        # everything should be running / healthy
```

Install the cert into Nginx and reload:

```bash
dc cp /etc/letsencrypt/live/arteq.yourdomain.com/fullchain.pem nginx:/etc/nginx/certs/fullchain.pem
dc cp /etc/letsencrypt/live/arteq.yourdomain.com/privkey.pem  nginx:/etc/nginx/certs/privkey.pem
dc restart nginx
```

## 7. Register the Vobiz SIP trunk

```bash
TOKEN=$(curl -sf -X POST https://arteq.yourdomain.com/admin/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"<DASHBOARD_ADMIN_PASSWORD>"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST https://arteq.yourdomain.com/admin/sip/vobiz/setup -H "Authorization: Bearer $TOKEN"
```

Copy the returned `LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID` into `.env`, then `dc up -d`
again.

## 8. Onboard the hospital

- Open `https://arteq.yourdomain.com/admin/` and log in as the super-admin.
- Create the hospital: `./setup.sh --hospital` (name, **slug**, doctors, and their
  **schedules** — without schedules `check_availability` returns no open slots).
- Create the hospital's own scoped login: `POST /admin/users`, role `tenant_admin`,
  mapped to that hospital's slug. Give that login to the hospital — never share the
  super-admin password. (Repeat per hospital for multi-tenant on one VPS.)

## 9. Telephony cut-over (Vobiz)

Call flow: `hospital landline → telco call-forward → Vobiz DID → Vobiz SIP trunk →
LiveKit SIP → room → Arya`.

- In the Vobiz console, route the hospital's DID to `<VPS_IP>:5060`.
- Set the hospital landline's call-forwarding to the Vobiz DID.

## 10. Verify before going live

```bash
dc logs -f agent | grep worker_registered     # confirm the agent registered
```

Place a real test call: Arya greets in Malayalam → name a doctor → **she replies
with availability** → book → you receive the WhatsApp/SMS confirmation, and the
dashboard shows the call + booking under the right hospital.

## Troubleshooting

- **Silent after you speak** → `dc logs --tail=50 agent`. A 429 / `API key` error
  means the Gemini key isn't funded — switch to the OpenRouter option (Step 4) and
  `dc up -d`.
- **No audio / call drops instantly** → a UDP port range isn't open (check BOTH ufw
  and the Hostinger panel firewall), or `NODE_IP` is wrong.
- **`check_availability` finds no slots** → the doctor has no `schedules` rows for
  that weekday; add them in the admin dashboard.
- **DB connection errors** → `DATABASE_URL` host must be `postgres` (the service
  name), and its user/password/db must match the `POSTGRES_*` values.

## Day-2 operations

```bash
dc ps                 # status
dc logs -f agent      # live agent logs
dc restart agent      # restart just the agent
dc down && dc up -d   # full restart
```

TLS auto-renew: schedule a monthly `certbot renew` followed by the two `dc cp`
lines from Step 6 and `dc restart nginx`.
