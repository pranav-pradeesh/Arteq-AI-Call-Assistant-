#!/usr/bin/env bash
# Arteq AI — One-command VPS installer
# Installs: Docker, PostgreSQL, Redis, LiveKit, LiveKit SIP, App, Agent, Nginx + SSL
# Usage: bash install.sh
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[•]${RESET} $*"; }
success() { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*"; exit 1; }
ask()     { echo -e "${BOLD}${YELLOW}[?]${RESET} $*"; }

# ── Banner ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${BLUE}║       Arteq AI — VPS Installer v1.0          ║${RESET}"
echo -e "${BOLD}${BLUE}║  Sets up everything for 3-5 hospitals         ║${RESET}"
echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════╝${RESET}"
echo ""

# ── Root check ─────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  error "Run as root: sudo bash install.sh"
fi

# ── OS check ───────────────────────────────────────────────────────────────────
if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
  warn "This script is tested on Ubuntu 22.04. Proceed anyway? (y/N)"
  read -r confirm; [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
fi

# ── RAM check ──────────────────────────────────────────────────────────────────
TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
if [[ "$TOTAL_RAM_GB" -lt 7 ]]; then
  warn "Only ${TOTAL_RAM_GB}GB RAM detected. 8GB+ recommended for 3-5 hospitals."
  warn "Continue anyway? (y/N)"
  read -r confirm; [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
fi

# ── Detect public IP ───────────────────────────────────────────────────────────
DETECTED_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || \
              curl -s --max-time 5 https://ifconfig.me 2>/dev/null || echo "")

# ── Collect config ─────────────────────────────────────────────────────────────
echo -e "${BOLD}── Configuration ──────────────────────────────────${RESET}"
echo ""

ask "Your VPS public IP [${DETECTED_IP:-enter manually}]:"
read -r NODE_IP
NODE_IP="${NODE_IP:-$DETECTED_IP}"
[[ -z "$NODE_IP" ]] && error "IP address is required."

ask "Your domain name (e.g. app.arteqai.com) — leave blank to skip SSL:"
read -r DOMAIN

ask "Sarvam AI API key (get from sarvam.ai):"
read -r SARVAM_API_KEY
[[ -z "$SARVAM_API_KEY" ]] && error "Sarvam API key is required."

ask "Groq API key (get from console.groq.com):"
read -r GROQ_API_KEY
[[ -z "$GROQ_API_KEY" ]] && error "Groq API key is required."

ask "Plivo Auth ID:"
read -r PLIVO_AUTH_ID

ask "Plivo Auth Token:"
read -r PLIVO_AUTH_TOKEN

ask "Plivo phone number for first hospital (e.g. +914844000000):"
read -r PLIVO_PHONE_NUMBER

ask "Staff alert phone number (SMS on bookings/emergencies, e.g. +919876543210):"
read -r STAFF_ALERT_PHONE

ask "Dashboard admin password (min 12 chars):"
read -rs DASHBOARD_ADMIN_PASSWORD; echo ""
[[ ${#DASHBOARD_ADMIN_PASSWORD} -lt 8 ]] && error "Password too short."

ask "Default language code [ml-IN = Malayalam, hi-IN = Hindi, en-IN = English]:"
read -r AGENT_LANGUAGE
AGENT_LANGUAGE="${AGENT_LANGUAGE:-ml-IN}"

ask "Agent name [Arya]:"
read -r AGENT_NAME
AGENT_NAME="${AGENT_NAME:-Arya}"

ask "Install pgAdmin (visual database UI)? (y/N):"
read -r INSTALL_PGADMIN
PGADMIN_ENABLED=false
PGADMIN_PASSWORD=""
if [[ "$INSTALL_PGADMIN" =~ ^[Yy]$ ]]; then
  PGADMIN_ENABLED=true
  ask "pgAdmin admin password:"
  read -rs PGADMIN_PASSWORD; echo ""
fi

echo ""
info "Generating secure secrets..."
POSTGRES_PASSWORD=$(openssl rand -hex 24)
LIVEKIT_API_KEY="arteq"
LIVEKIT_API_SECRET=$(openssl rand -hex 32)
DASHBOARD_JWT_SECRET=$(openssl rand -hex 32)

echo ""
info "Summary of what will be installed:"
echo "  • PostgreSQL (database)"
echo "  • Redis (caching)"
echo "  • LiveKit server + SIP (voice infrastructure)"
echo "  • Arteq web app + AI agent (Arya)"
[[ "$PGADMIN_ENABLED" == "true" ]] && echo "  • pgAdmin (database UI)"
[[ -n "$DOMAIN" ]] && echo "  • Nginx + Let's Encrypt SSL for $DOMAIN"
echo ""
ask "Ready to install? (y/N):"
read -r confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }

# ── Step 1: System packages ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 1/6: System packages ───────────────────────${RESET}"
info "Updating package list..."
apt-get update -qq

info "Installing dependencies..."
apt-get install -y -qq curl git ufw nginx certbot python3-certbot-nginx openssl 2>/dev/null
success "System packages ready."

# ── Step 2: Docker ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 2/6: Docker ────────────────────────────────${RESET}"
if command -v docker &>/dev/null; then
  success "Docker already installed: $(docker --version)"
else
  info "Installing Docker..."
  curl -fsSL https://get.docker.com | sh -s -- -q
  systemctl enable docker --now
  success "Docker installed."
fi

# ── Step 3: Firewall ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 3/6: Firewall ──────────────────────────────${RESET}"
info "Configuring UFW firewall..."
ufw --force reset > /dev/null
ufw default deny incoming > /dev/null
ufw default allow outgoing > /dev/null
ufw allow 22/tcp    comment "SSH"          > /dev/null
ufw allow 80/tcp    comment "HTTP"         > /dev/null
ufw allow 443/tcp   comment "HTTPS"        > /dev/null
ufw allow 7880/tcp  comment "LiveKit WS"   > /dev/null
ufw allow 7881/tcp  comment "LiveKit RTC"  > /dev/null
ufw allow 5060/udp  comment "SIP Plivo"    > /dev/null
ufw allow 50000:50200/udp comment "WebRTC media" > /dev/null
ufw allow 10000:10100/udp comment "SIP RTP"      > /dev/null
[[ "$PGADMIN_ENABLED" == "true" ]] && ufw allow 5050/tcp comment "pgAdmin" > /dev/null
ufw --force enable > /dev/null
success "Firewall configured."

# ── Step 4: Repo + .env ────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 4/6: App setup ─────────────────────────────${RESET}"

INSTALL_DIR="/opt/arteq"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Repo already exists — pulling latest..."
  git -C "$INSTALL_DIR" pull --quiet
else
  info "Cloning repository..."
  git clone --quiet https://github.com/pranav-pradeesh/Arteq-AI-Call-Assistant-.git "$INSTALL_DIR"
fi

PUBLIC_BASE_URL="http://${NODE_IP}:8000"
PUBLIC_WS_URL="ws://${NODE_IP}:7880"
if [[ -n "$DOMAIN" ]]; then
  PUBLIC_BASE_URL="https://${DOMAIN}"
  PUBLIC_WS_URL="wss://${DOMAIN}/livekit"
fi

info "Writing .env..."
cat > "${INSTALL_DIR}/.env" <<EOF
ENV=production
LOG_LEVEL=INFO
TELEPHONY_MODE=stream

NODE_IP=${NODE_IP}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL}
PUBLIC_WS_URL=${PUBLIC_WS_URL}

POSTGRES_USER=arteq
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=arteq_hospital

LIVEKIT_API_KEY=${LIVEKIT_API_KEY}
LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET}
LIVEKIT_SIP_HOST=${NODE_IP}
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=

SARVAM_API_KEY=${SARVAM_API_KEY}
GROQ_API_KEY=${GROQ_API_KEY}
TTS_PROVIDER=sarvam
STT_PROVIDER=sarvam

PLIVO_AUTH_ID=${PLIVO_AUTH_ID}
PLIVO_AUTH_TOKEN=${PLIVO_AUTH_TOKEN}
PLIVO_PHONE_NUMBER=${PLIVO_PHONE_NUMBER}
WHATSAPP_ENABLED=true
PLIVO_WHATSAPP_NUMBER=${PLIVO_PHONE_NUMBER}
WHATSAPP_FALLBACK_TO_SMS=true

AGENT_NAME=${AGENT_NAME}
AGENT_LANGUAGE=${AGENT_LANGUAGE}
DEFAULT_LANGUAGE=${AGENT_LANGUAGE}

DASHBOARD_ADMIN_PASSWORD=${DASHBOARD_ADMIN_PASSWORD}
DASHBOARD_JWT_SECRET=${DASHBOARD_JWT_SECRET}
STAFF_ALERT_PHONE=${STAFF_ALERT_PHONE}

SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
EOF
success ".env written."

# ── Optionally add pgAdmin to docker-compose ───────────────────────────────────
COMPOSE_FILE="${INSTALL_DIR}/docker-compose.selfhost.yml"
if [[ "$PGADMIN_ENABLED" == "true" ]]; then
  if ! grep -q "pgadmin" "$COMPOSE_FILE"; then
    info "Adding pgAdmin to compose file..."
    # Append pgAdmin service before the final 'volumes:' block
    PGADMIN_SERVICE="
  pgadmin:
    image: dpage/pgadmin4:latest
    restart: unless-stopped
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@arteqai.com
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_PASSWORD}
    ports:
      - \"5050:80\"
    depends_on:
      - postgres
"
    # Insert before 'volumes:' at end of file
    sed -i "s/^volumes:/${PGADMIN_SERVICE}\nvolumes:/" "$COMPOSE_FILE"
  fi
fi

# ── Step 5: Start containers ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 5/6: Starting containers ───────────────────${RESET}"
cd "$INSTALL_DIR"
info "Pulling images (this may take a few minutes)..."
docker compose -f docker-compose.selfhost.yml pull --quiet 2>/dev/null || true
info "Building app image..."
docker compose -f docker-compose.selfhost.yml build --quiet
info "Starting all services..."
docker compose -f docker-compose.selfhost.yml up -d

# Wait for app to be healthy
info "Waiting for app to be ready..."
for i in {1..30}; do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    break
  fi
  sleep 3
done

if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
  success "App is up and healthy."
else
  warn "App health check timed out — check logs: docker compose -f ${INSTALL_DIR}/docker-compose.selfhost.yml logs app"
fi

# ── Step 6: Nginx + SSL ────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 6/6: Nginx + SSL ───────────────────────────${RESET}"

NGINX_CONF="/etc/nginx/sites-available/arteq"
cat > "$NGINX_CONF" <<'NGINXEOF'
server {
    listen 80;
    server_name DOMAIN_PLACEHOLDER;
    client_max_body_size 20M;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # LiveKit WebSocket signaling
    location /livekit/ {
        proxy_pass http://localhost:7880/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
NGINXEOF

if [[ -n "$DOMAIN" ]]; then
  sed -i "s/DOMAIN_PLACEHOLDER/${DOMAIN}/" "$NGINX_CONF"
else
  sed -i "s/DOMAIN_PLACEHOLDER/_/" "$NGINX_CONF"
fi

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/arteq
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
success "Nginx configured."

if [[ -n "$DOMAIN" ]]; then
  info "Getting SSL certificate for ${DOMAIN}..."
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}" 2>/dev/null; then
    success "SSL certificate installed."
  else
    warn "SSL failed — make sure ${DOMAIN} points to ${NODE_IP} and port 80 is open."
    warn "Run manually later: certbot --nginx -d ${DOMAIN}"
  fi
fi

# ── SIP trunk setup ─────────────────────────────────────────────────────────────
info "Setting up SIP trunks with Plivo..."
sleep 5
curl -sf -X POST "http://localhost:8000/admin/sip/setup" > /dev/null 2>&1 && \
  success "SIP trunks configured." || \
  warn "SIP setup failed — run manually: curl -X POST ${PUBLIC_BASE_URL}/admin/sip/setup"

# ── Save credentials ────────────────────────────────────────────────────────────
CREDS_FILE="${INSTALL_DIR}/CREDENTIALS.txt"
cat > "$CREDS_FILE" <<EOF
Arteq AI — Installation Credentials
Generated: $(date)
Keep this file safe. Do not commit to git.

── App ────────────────────────────────
Dashboard URL : ${PUBLIC_BASE_URL}/admin/
Admin password: ${DASHBOARD_ADMIN_PASSWORD}

── Database ───────────────────────────
Host          : localhost:5432
User          : arteq
Password      : ${POSTGRES_PASSWORD}
Database      : arteq_hospital

── LiveKit ────────────────────────────
URL           : ws://${NODE_IP}:7880
API Key       : ${LIVEKIT_API_KEY}
API Secret    : ${LIVEKIT_API_SECRET}

EOF
[[ "$PGADMIN_ENABLED" == "true" ]] && cat >> "$CREDS_FILE" <<EOF
── pgAdmin ────────────────────────────
URL           : http://${NODE_IP}:5050
Email         : admin@arteqai.com
Password      : ${PGADMIN_PASSWORD}

EOF
chmod 600 "$CREDS_FILE"
success "Credentials saved to ${CREDS_FILE}"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           Installation Complete!             ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Dashboard:${RESET} ${PUBLIC_BASE_URL}/admin/"
echo -e "  ${BOLD}Password :${RESET} ${DASHBOARD_ADMIN_PASSWORD}"
[[ "$PGADMIN_ENABLED" == "true" ]] && \
  echo -e "  ${BOLD}pgAdmin  :${RESET} http://${NODE_IP}:5050"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo "  1. Open the dashboard and add your hospitals"
echo "  2. For each hospital, buy a Plivo DID number"
echo "  3. Set Plivo webhook → ${PUBLIC_BASE_URL}/plivo/inbound/{hospital-slug}"
echo ""
echo -e "  ${BOLD}Useful commands:${RESET}"
echo "  View logs   : docker compose -f ${INSTALL_DIR}/docker-compose.selfhost.yml logs -f"
echo "  Restart all : docker compose -f ${INSTALL_DIR}/docker-compose.selfhost.yml restart"
echo "  Stop all    : docker compose -f ${INSTALL_DIR}/docker-compose.selfhost.yml down"
echo ""
echo -e "  ${BOLD}Credentials file:${RESET} ${CREDS_FILE}"
echo ""
