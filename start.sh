#!/usr/bin/env bash
# Arteq Hospital Voice Agent — one-command launcher (macOS / Linux).
# Usage:  ./start.sh            (web only)
#         ./start.sh --with-agent   (run Arya too, full end-to-end)
set -e
cd "$(dirname "$0")"

PY=python3
if ! command -v python3 >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then PY=python; else
    echo "Python 3.10+ is required but was not found on PATH." >&2
    exit 1
  fi
fi

exec "$PY" run.py "$@"
