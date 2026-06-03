# Arteq Voice Agent — Oracle Cloud Always-Free Deploy Guide

Run the **entire stack on one free Oracle box**: FastAPI web + LiveKit agent
worker + **self-hosted LiveKit server** + **local Postgres**. Only the AI APIs
(Groq, Sarvam) and optional phone (Plivo) stay external.

**Cost:** server ₹0 · LiveKit ₹0 · Postgres ₹0 · LLM/STT/TTS pennies/min ·
phone optional. Browser calls all-in ≈ ₹0–₹1.2/min.

> Oracle Always-Free ARM instance: **4 OCPU, 24 GB RAM, 200 GB disk, 10 TB
> egress/mo — free forever.** Signup verifies a card but is never charged on the
> Always-Free shapes.

---

## What runs where

| On the Oracle box | External (not Oracle) |
|---|---|
| FastAPI web (`/talk`, `/admin`, webhooks) | Groq LLM API |
| LiveKit agent worker (Arya) | Sarvam STT + TTS API |
| **LiveKit server (self-hosted OSS)** | Plivo phone numbers (optional) |
| Postgres database | |
| Outbound scheduler | |

You need **one domain name** (or a free DuckDNS subdomain). Browser mic access
and `wss://` both require HTTPS — that is why a domain + TLS is mandatory.

---

## 0. Prerequisites

- Oracle Cloud account (free): https://www.oracle.com/cloud/free/
- A domain. Free option: https://www.duckdns.org (gives `yourname.duckdns.org`).
- Your API keys: `SARVAM_API_KEY`, `GROQ_API_KEY`.

This guide uses two subdomains. Replace throughout:
- `app.example.com` → the web app + dashboard
- `lk.example.com` → the LiveKit server

(With DuckDNS you get one subdomain — use `arteq.duckdns.org` for the app and
run LiveKit on a path/port; see note in §6. Two subdomains is cleaner; a ₹100/yr
domain is worth it.)

---

## 1. Create the Always-Free ARM instance

1. Oracle console → **Compute → Instances → Create instance**.
2. **Image:** Canonical **Ubuntu 22.04**.
3. **Shape:** change to **Ampere → VM.Standard.A1.Flex**, set **4 OCPU, 24 GB**
   (the full free allowance).
4. **Networking:** create/assign a VCN with a **public IPv4**.
5. **SSH keys:** upload your public key (or let Oracle generate one — save it).
6. Create. Note the **public IP**.

Point your DNS at it:
- `app.example.com` → A record → `<public-ip>`
- `lk.example.com`  → A record → `<public-ip>`

---

## 2. Open the firewall (TWO layers — both required)

Oracle blocks everything by default at the cloud level **and** Ubuntu ships
iptables rules. Open both.

### 2a. Oracle Security List (cloud level)

Console → your VCN → **Security Lists** → default list → **Add Ingress Rules**
(source `0.0.0.0/0`):

| Port / range | Protocol | Why |
|---|---|---|
| 80 | TCP | HTTP (TLS cert challenge) |
| 443 | TCP | HTTPS web + `wss` LiveKit signaling |
| 7881 | TCP | LiveKit RTC over TCP fallback |
| 50000–60000 | UDP | LiveKit RTC media (WebRTC) |

### 2b. Ubuntu firewall (on the box, after SSH in §3)

```bash
sudo iptables -I INPUT -p tcp --dport 80   -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443  -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 7881 -j ACCEPT
sudo iptables -I INPUT -p udp --dport 50000:60000 -j ACCEPT
sudo netfilter-persistent save   # persist across reboot (install if missing: sudo apt install -y iptables-persistent)
```

---

## 3. SSH in + install system packages

```bash
ssh ubuntu@<public-ip>

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git ffmpeg \
                    postgresql postgresql-contrib build-essential \
                    libpq-dev iptables-persistent
```

(Optional but recommended — add 4 GB swap so pip/builds don't OOM:)
```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 4. Local Postgres

```bash
sudo -u postgres psql <<'SQL'
CREATE USER arteq WITH PASSWORD 'CHANGE_ME_STRONG';
CREATE DATABASE arteq_hospital OWNER arteq;
GRANT ALL PRIVILEGES ON DATABASE arteq_hospital TO arteq;
SQL
```

Connection string for `.env` (local → SSL off):
```
DATABASE_URL=postgresql://arteq:CHANGE_ME_STRONG@localhost:5432/arteq_hospital
DB_SSL=disable
```

> Migrations apply automatically on app boot — no manual step.

---

## 5. Self-host the LiveKit server

Install:
```bash
curl -sSL https://get.livekit.io | sudo bash
livekit-server --version   # confirm
```

Generate an API key/secret pair:
```bash
livekit-server generate-keys
# prints:  API Key: APIxxxx   Secret: yyyy   — copy both
```

Create `/etc/livekit.yaml` (replace the key/secret):
```yaml
port: 7880
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true          # advertise the Oracle public IP to clients
keys:
  APIxxxx: yyyy
```

systemd service `/etc/systemd/system/livekit.service`:
```ini
[Unit]
Description=LiveKit Server
After=network.target

[Service]
ExecStart=/usr/local/bin/livekit-server --config /etc/livekit.yaml
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now livekit
sudo systemctl status livekit --no-pager
```

LiveKit now listens on `localhost:7880`. Caddy (§7) puts TLS in front so the
browser can reach `wss://lk.example.com`.

---

## 6. Clone the app + configure

```bash
cd /opt
sudo git clone https://github.com/pranav-pradeesh/Arteq-AI-Call-Assistant-.git arteq
sudo chown -R ubuntu:ubuntu /opt/arteq
cd /opt/arteq

python3.11 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create `.env` (`cp .env.example .env` then edit). The values that **change for
self-hosting**:

```bash
ENV=production
PORT=8000
PUBLIC_BASE_URL=https://app.example.com
PUBLIC_WS_URL=wss://app.example.com

# Self-hosted LiveKit (NOT *.livekit.cloud) — key/secret from §5
LIVEKIT_URL=wss://lk.example.com
LIVEKIT_API_KEY=APIxxxx
LIVEKIT_API_SECRET=yyyy

# Local Postgres from §4
DATABASE_URL=postgresql://arteq:CHANGE_ME_STRONG@localhost:5432/arteq_hospital
DB_SSL=disable

# AI APIs (external)
SARVAM_API_KEY=your_sarvam_key
GROQ_API_KEY=your_groq_key

# Generate secrets:
#   python -c "import secrets; print(secrets.token_hex(32))"
DASHBOARD_JWT_SECRET=...
INTERNAL_API_KEY=...
DASHBOARD_ADMIN_PASSWORD=...
```

> **DuckDNS single-subdomain note:** if you only have one subdomain, set both
> `app` and `lk` to it but run LiveKit behind a path is NOT supported by the
> SDK — instead give LiveKit its own port. Easiest: get a cheap domain so you
> can use two subdomains. If stuck on one, run LiveKit on `443` of a second
> free DuckDNS name (you can register up to 5 DuckDNS subdomains free).

---

## 7. TLS + reverse proxy (Caddy)

Caddy auto-provisions Let's Encrypt certs.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:
```
app.example.com {
    reverse_proxy localhost:8000
}

lk.example.com {
    reverse_proxy localhost:7880
}
```

```bash
sudo systemctl reload caddy
```

Caddy fetches HTTPS certs automatically (needs ports 80/443 open — §2).

---

## 8. Run the app as services

Web `/etc/systemd/system/arteq-web.service`:
```ini
[Unit]
Description=Arteq Web
After=network.target postgresql.service

[Service]
WorkingDirectory=/opt/arteq
EnvironmentFile=/opt/arteq/.env
ExecStart=/opt/arteq/.venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

Worker `/etc/systemd/system/arteq-agent.service`:
```ini
[Unit]
Description=Arteq LiveKit Agent (Arya)
After=network.target livekit.service

[Service]
WorkingDirectory=/opt/arteq
EnvironmentFile=/opt/arteq/.env
ExecStart=/opt/arteq/.venv/bin/python livekit_agent.py start
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

> Worker uses `start` (production pool), not `dev`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now arteq-web arteq-agent
sudo systemctl status arteq-web arteq-agent --no-pager
```

---

## 9. Test

1. Open `https://app.example.com/talk` in a browser (mic permission needs
   HTTPS — now satisfied).
2. Click to call. Arya should greet in Malayalam and answer.
3. Logs:
   ```bash
   journalctl -u arteq-agent -f      # worker / Arya
   journalctl -u arteq-web -f        # web
   journalctl -u livekit -f          # LiveKit server
   ```

---

## 10. Optional — real phone numbers (Plivo)

Browser is ₹0 and needs no phone. For PSTN:
1. Buy a Plivo India DID (₹250/mo) + credit.
2. Fill `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER` in `.env`.
3. Point the Plivo number's webhook at
   `https://app.example.com/api/v1/call/inbound/<tenant_slug>`.
4. Restart: `sudo systemctl restart arteq-web arteq-agent`.

---

## 11. Updating

```bash
cd /opt/arteq
git pull origin main
. .venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart arteq-web arteq-agent
```

> Railway currently deploys from `main`. On Oracle you pull `main` too — keep
> shipping fixes to `main`.

---

## Cost recap (Oracle config)

| Item | Cost |
|---|---|
| Server (Oracle Always-Free) | **₹0/mo** |
| LiveKit (self-hosted) | **₹0/mo** |
| Postgres (local) | **₹0/mo** |
| Domain | ₹0 (DuckDNS) – ~₹100/yr |
| Groq LLM | pennies/min (free tier, or Dev tier for concurrency) |
| Sarvam STT+TTS | ~₹1.12/min |
| Plivo phone (optional) | ₹250/mo + ₹0.34–0.71/min |

**Browser-only, free LLM tier ≈ ₹1.12/min, ₹0 fixed.** Add Groq Dev tier
(pennies) to kill the rate-limit stall and run concurrent calls.
