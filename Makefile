.PHONY: dev agent test lint install db-up db-seed token help

# ── Dev ───────────────────────────────────────────────────────────────────────
dev:
	@echo "Starting FastAPI web server on :8000..."
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

agent:
	@echo "Starting LiveKit agent worker..."
	python livekit_agent.py dev

# Run both in parallel (requires GNU make ≥ 4.0)
# Usage: make -j dev agent
all: dev agent

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest tests/test_smoke.py -v

test-ci:
	pytest tests/ -v --tb=short

# ── Code quality ──────────────────────────────────────────────────────────────
lint:
	ruff check . --fix
	ruff format .

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt

# ── DB helpers ────────────────────────────────────────────────────────────────
db-migrate:
	@echo "Migrations run automatically on startup via lifespan(). Just start the server."

db-seed:
	@echo "Demo hospital already seeded by 001_schema.sql (ID 00000000-0000-0000-0000-000000000001)"

# ── LiveKit test token (browser testing) ─────────────────────────────────────
token:
	@echo "Fetching LiveKit token for slug=demo..."
	curl -s "http://localhost:8000/api/v1/livekit/token?slug=demo&participant=me" | python3 -m json.tool

# ── Help ─────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  make install     — install Python dependencies"
	@echo "  make dev         — start FastAPI server (port 8000, auto-reload)"
	@echo "  make agent       — start LiveKit agent worker"
	@echo "  make -j dev agent — start both in parallel"
	@echo "  make test        — run smoke tests"
	@echo "  make lint        — run ruff linter + formatter"
	@echo "  make token       — get a LiveKit browser test token"
	@echo ""
