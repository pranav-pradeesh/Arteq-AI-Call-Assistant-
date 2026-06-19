#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Arteq Hospital Voice Agent — one-shot setup script
#
# What this does:
#   1. Checks required env variables are set (fails fast if missing)
#   2. Creates Python virtualenv and installs dependencies
#   3. Probes the database and runs all migrations automatically
#   4. Upserts the superadmin account
#   5. Verifies LiveKit connectivity
#   6. Runs POST /admin/sip/vobiz/setup to register Vobiz SIP trunks
#   7. Optionally creates the first hospital via the wizard API
#
# Usage:
#   cp .env.example .env        # fill in your secrets
#   chmod +x setup.sh
#   ./setup.sh                  # non-interactive (just migrations + SIP setup)
#   ./setup.sh --hospital       # also create the first hospital interactively
#   ./setup.sh --docker         # use docker compose instead of local venv
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

HOSPITAL_MODE=0
DOCKER_MODE=0
for arg in "$@"; do
  [[ "$arg" == "--hospital" ]] && HOSPITAL_MODE=1
  [[ "$arg" == "--docker" ]]   && DOCKER_MODE=1
done

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║          Arteq Hospital Voice Agent — Setup              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Load .env ──────────────────────────────────────────────────────────────
if [[ ! -f ".env" ]]; then
  warn ".env not found — copying from .env.example"
  cp .env.example .env
  fail "Edit .env with your secrets, then re-run this script."
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# ── 2. Required variable check ────────────────────────────────────────────────
MISSING=()
for var in DATABASE_URL LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET \
           SARVAM_API_KEY GOOGLE_API_KEY DASHBOARD_ADMIN_PASSWORD DASHBOARD_JWT_SECRET \
           INTERNAL_API_KEY; do
  [[ -z "${!var:-}" ]] && MISSING+=("$var")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  fail "Missing required env vars in .env:\n$(printf '  • %s\n' "${MISSING[@]}")"
fi
ok "All required env vars are set"

# Auto-generate secrets if they are still at default values
if [[ "${DASHBOARD_JWT_SECRET:-}" == "change_me_in_production" ]]; then
  NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  sed -i "s|DASHBOARD_JWT_SECRET=.*|DASHBOARD_JWT_SECRET=${NEW_SECRET}|" .env
  export DASHBOARD_JWT_SECRET="$NEW_SECRET"
  ok "Generated new DASHBOARD_JWT_SECRET"
fi
if [[ "${INTERNAL_API_KEY:-}" == "your_internal_api_key_here" ]]; then
  NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  sed -i "s|INTERNAL_API_KEY=.*|INTERNAL_API_KEY=${NEW_KEY}|" .env
  export INTERNAL_API_KEY="$NEW_KEY"
  ok "Generated new INTERNAL_API_KEY"
fi

# ── 3. Python environment ─────────────────────────────────────────────────────
if [[ "$DOCKER_MODE" -eq 0 ]]; then
  if [[ ! -d ".venv" ]]; then
    info "Creating Python virtualenv..."
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  info "Installing Python dependencies..."
  pip install -q --no-cache-dir -r requirements.txt
  ok "Python environment ready"
fi

# ── 4. Database migrations (run the app briefly to apply) ────────────────────
info "Running database migrations..."

if [[ "$DOCKER_MODE" -eq 1 ]]; then
  docker compose -f docker-compose.selfhost.yml run --rm app \
    python -c "
import asyncio, pathlib, asyncpg

async def run():
    url = '${DATABASE_URL}'.replace('postgresql://', 'postgresql://')
    conn = await asyncpg.connect(dsn=url)
    mdir = pathlib.Path('migrations/versions')
    for f in sorted(mdir.glob('*.sql')):
        await conn.execute(f.read_text())
        print(f'  applied {f.name}')
    await conn.close()
    print('Migrations complete.')

asyncio.run(run())
"
else
  python3 - <<'PYEOF'
import asyncio, pathlib, os, sys
import asyncpg

async def run():
    url = os.environ["DATABASE_URL"]
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=url), timeout=15)
    except Exception as e:
        print(f"  DB connection failed: {e}", file=sys.stderr)
        sys.exit(1)
    mdir = pathlib.Path("migrations/versions")
    for f in sorted(mdir.glob("*.sql")):
        await conn.execute(f.read_text())
        print(f"  applied {f.name}")
    await conn.close()

asyncio.run(run())
PYEOF
fi
ok "Database migrations complete"

# ── 5. Upsert superadmin (via the app's own logic at startup) ─────────────────
info "Superadmin account will be upserted automatically on first server start."
info "  Email:    ${SUPERADMIN_EMAIL:-admin@arteqai.com}"
info "  Password: [from DASHBOARD_ADMIN_PASSWORD]"

# ── 6. LiveKit connectivity check ─────────────────────────────────────────────
info "Checking LiveKit connectivity..."
python3 - <<'PYEOF'
import os, sys
url = os.environ.get("LIVEKIT_URL", "")
if not url:
    print("  LIVEKIT_URL not set — skipping check")
    sys.exit(0)

import httpx, asyncio

async def check():
    probe = url.replace("wss://", "https://").replace("ws://", "http://") + "/"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(probe)
        print(f"  LiveKit reachable (HTTP {r.status_code})")
    except Exception as e:
        print(f"  LiveKit probe failed: {e} — check LIVEKIT_URL")

asyncio.run(check())
PYEOF
ok "LiveKit check done"

# ── 7. SIP / Vobiz trunk setup ────────────────────────────────────────────────
BASE_URL="${PUBLIC_BASE_URL:-http://localhost:8000}"
ADMIN_PW="${DASHBOARD_ADMIN_PASSWORD}"

if [[ -n "${VOBIZ_API_KEY:-}" && -n "${VOBIZ_PHONE_NUMBER:-}" ]]; then
  info "Setting up Vobiz SIP trunks via /admin/sip/vobiz/setup ..."

  # Login to get a token
  TOKEN=$(curl -sf -X POST "${BASE_URL}/admin/login" \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"${ADMIN_PW}\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null || echo "")

  if [[ -z "$TOKEN" ]]; then
    warn "Could not obtain admin token — server may not be running yet."
    warn "After starting the server, run manually:"
    warn "  curl -X POST ${BASE_URL}/admin/sip/vobiz/setup -H 'Authorization: Bearer <token>'"
  else
    RESP=$(curl -sf -X POST "${BASE_URL}/admin/sip/vobiz/setup" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" 2>&1 || echo "FAILED")
    if [[ "$RESP" == "FAILED" ]]; then
      warn "SIP setup request failed — run manually after server starts."
    else
      echo "$RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    tid = d.get('livekit_sip_vobiz_outbound_trunk_id') or d.get('outbound_trunk_id') or ''
    if tid:
        print(f'  Vobiz outbound trunk ID: {tid}')
        print(f'  → Set LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID={tid} in .env')
    else:
        print('  Response:', json.dumps(d, indent=2))
except: print(sys.stdin.read())
" 2>/dev/null || echo "$RESP"
      ok "Vobiz SIP trunks configured"
    fi
  fi
else
  warn "VOBIZ_API_KEY / VOBIZ_PHONE_NUMBER not set — skipping SIP trunk setup."
  warn "Set them in .env, then POST /admin/sip/vobiz/setup to register trunks."
fi

# ── 8. Optional: create first hospital ───────────────────────────────────────
if [[ "$HOSPITAL_MODE" -eq 1 ]]; then
  echo ""
  echo "── Hospital Creation Wizard ────────────────────────────────"
  read -rp "Hospital name (English): " HOSP_NAME
  read -rp "Short slug (e.g. malabar-hospital): " HOSP_SLUG
  read -rp "Address: " HOSP_ADDR
  read -rp "Phone (E.164, e.g. +914952XXXXXX): " HOSP_PHONE

  TOKEN=$(curl -sf -X POST "${BASE_URL}/admin/login" \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"${ADMIN_PW}\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null || echo "")

  if [[ -z "$TOKEN" ]]; then
    fail "Cannot create hospital — server not reachable at ${BASE_URL}"
  fi

  WIZARD_RESP=$(curl -sf -X POST "${BASE_URL}/admin/hospitals/wizard" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${HOSP_NAME}\",\"slug\":\"${HOSP_SLUG}\",\"address\":\"${HOSP_ADDR}\",\"phone\":\"${HOSP_PHONE}\",\"tier\":\"hospital\"}" 2>&1)

  echo "$WIZARD_RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('  Hospital ID:', d.get('hospital_id',''))
    print('  Slug:       ', d.get('slug',''))
except: print(sys.stdin.read())
" 2>/dev/null || echo "$WIZARD_RESP"
  ok "Hospital created: ${HOSP_SLUG}"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                  Setup Complete!                         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
ok "Next steps:"
echo "  1. Start the server:  docker compose -f docker-compose.selfhost.yml up -d"
echo "     (or locally:)      make dev   +   make agent"
echo "  2. Open admin dashboard: ${BASE_URL}/admin/"
echo "  3. Add hospitals:     POST /admin/hospitals/wizard"
echo "  4. Configure Vobiz:   Set call forwarding from hospital landline to Vobiz DID"
echo ""
