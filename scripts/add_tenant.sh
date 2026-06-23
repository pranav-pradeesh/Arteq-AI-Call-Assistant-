#!/usr/bin/env bash
# add_tenant.sh — provision a new hospital/clinic tenant on the running stack.
#
# Usage (run on the VPS, from /root/arteq):
#   ./scripts/add_tenant.sh --name "City Clinic" --slug city-clinic \
#       --admin-user cityclinic --admin-pass 'Secret@123' \
#       [--phone +914871234567] [--address "..."] [--language ml-IN] \
#       [--tier clinic] [--trial-days 14] [--kb-file /tmp/kb.txt]
#
# It copies the provisioning script into the app container (which holds the
# DATABASE_URL + asyncpg + bcrypt) and runs it there. Idempotent on --slug.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_CONTAINER="${APP_CONTAINER:-arteq-app-1}"

if ! docker ps --format '{{.Names}}' | grep -q "^${APP_CONTAINER}$"; then
  echo "ERROR: container ${APP_CONTAINER} is not running." >&2
  exit 1
fi

# If a --kb-file is passed, copy it into the container first and rewrite the path.
ARGS=()
KB_TMP=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--kb-file" ]]; then
    KB_TMP="/tmp/_add_tenant_kb.txt"
    docker cp "$2" "${APP_CONTAINER}:${KB_TMP}"
    ARGS+=("--kb-file" "${KB_TMP}")
    shift 2
  else
    ARGS+=("$1")
    shift
  fi
done

docker cp "${SCRIPT_DIR}/add_tenant.py" "${APP_CONTAINER}:/tmp/add_tenant.py"
docker exec "${APP_CONTAINER}" python3 /tmp/add_tenant.py "${ARGS[@]}"
