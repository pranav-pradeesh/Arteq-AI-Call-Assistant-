#!/usr/bin/env bash
# update.sh — pull the latest code from GitHub and redeploy the whole stack.
#
# Usage (on the VPS):
#   cd /root/arteq && ./scripts/update.sh
#
# Safe to re-run. It fast-forwards the repo, rebuilds the app/agent/frontend
# images, applies idempotent DB migrations on app start, and reloads nginx.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
DC="docker compose -f docker-compose.selfhost.yml"

echo "==> [1/5] Pulling latest from GitHub..."
git fetch origin
git pull --ff-only

echo "==> [2/5] Rebuilding images (app, agent, frontend)..."
$DC build app agent frontend

echo "==> [3/5] Restarting application services..."
# Migrations run automatically on app startup (idempotent).
$DC up -d app agent frontend

echo "==> [4/5] Reloading nginx (picks up any deploy/nginx.conf change)..."
$DC up -d --force-recreate nginx

echo "==> [5/5] Status:"
$DC ps

echo ""
echo "Update complete. Dashboard: http://187.127.153.87/login"
