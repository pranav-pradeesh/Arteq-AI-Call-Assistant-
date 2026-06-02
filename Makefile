.PHONY: run run-agent dev agent test lint install db-migrate token help

# ── One-command launcher (cross-platform; same as ./start.sh / start.bat) ──────
run:
	python run.py --reload

run-agent:
	python run.py --with-agent --reload

# ── Development ───────────────────────────────────────────────────────────────
dev:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

agent:
	python livekit_agent.py dev

# Run both servers in parallel — requires GNU make ≥ 4.0
both:
	$(MAKE) -j2 dev agent

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest tests/test_smoke.py -v

test-all:
	pytest tests/ -v --tb=short

# ── Code quality ──────────────────────────────────────────────────────────────
lint:
	ruff check . --fix
	ruff format .

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt

# One-liner: install the LiveKit bundle (agents + all plugins at matching versions)
install-livekit:
	pip install "livekit-agents==1.5.16" "livekit-plugins-openai==1.1.7" \
	            "livekit-plugins-sarvam==1.1.7" "livekit-plugins-silero==1.1.7"

# ── DB ────────────────────────────────────────────────────────────────────────
db-migrate:
	@echo "Migrations run automatically on startup (lifespan). Just start the server."

# ── LiveKit token for browser testing ────────────────────────────────────────
token:
	curl -s "http://localhost:8000/api/v1/livekit/token?slug=demo&participant=me" | python3 -m json.tool

# ── Help ─────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  make run              one-command launcher (venv + deps + server + browser)"
	@echo "  make run-agent        launcher + LiveKit agent worker (full end-to-end)"
	@echo "  make install          install Python dependencies"
	@echo "  make install-livekit  install LiveKit agent + plugins (pinned versions)"
	@echo "  make dev              FastAPI server (port 8000, live-reload)"
	@echo "  make agent            LiveKit agent worker (dev mode)"
	@echo "  make both             both servers in parallel (GNU make -j2)"
	@echo "  make test             smoke tests"
	@echo "  make test-all         full test suite"
	@echo "  make lint             ruff check + format"
	@echo "  make token            get a LiveKit browser test token"
	@echo ""
