#!/usr/bin/env bash
# test_outbound.sh — place a test outbound call to your phone.
#   ./scripts/test_outbound.sh --phone +9198XXXXXXXX --mode now   --name "Pranav"
#   ./scripts/test_outbound.sh --phone +9198XXXXXXXX --mode queue --name "Pranav"
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="${APP_CONTAINER:-arteq-app-1}"
docker cp "${DIR}/test_outbound.py" "${APP}:/tmp/test_outbound.py"
docker exec -w /app "${APP}" python3 /tmp/test_outbound.py "$@"
