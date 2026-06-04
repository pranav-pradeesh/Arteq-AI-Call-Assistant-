#!/usr/bin/env python3
"""
Arteq self-diagnostic — one command that tells a tester exactly what works
and what to fix, then writes a redacted log they can copy/paste back.

Run it (sets up the venv first, then checks everything):

    python run.py doctor              # macOS / Linux / Windows

Or directly inside the venv:

    python tools/doctor.py

It NEVER prints secret values — only whether each one is set. The full report
is also written to ``arteq-diagnostic.log`` in the project root.
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_OK, _WARN, _ERR = "PASS", "WARN", "FAIL"
_buf = io.StringIO()


def _emit(line: str = "") -> None:
    print(line, flush=True)
    _buf.write(line + "\n")


def _row(status: str, name: str, detail: str = "") -> None:
    mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}.get(status, "[ ?? ]")
    _emit(f"  {mark} {name}" + (f" - {detail}" if detail else ""))


def _section(title: str) -> None:
    _emit("")
    _emit(title)
    _emit("-" * len(title))


def _is_placeholder(v: str) -> bool:
    v = (v or "").strip()
    return (not v) or v.startswith("your_") or v.startswith("wss://your-project") \
        or v in {"change-me-in-production", "admin"}


async def _check_db(results: list[str]) -> None:
    _section("Database (control)")
    try:
        from src.db.queries import get_control_pool
        pool = await asyncio.wait_for(get_control_pool(), timeout=15)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        _row(_OK, "Control DB reachable")
        results.append(_OK)
    except Exception as e:
        _row(_ERR, "Control DB reachable", str(e)[:160])
        results.append(_ERR)
        return

    # Tenants registry
    try:
        from src.tenancy import registry
        tenants = await registry.list_tenants(include_inactive=True)
        active = [t for t in tenants if t.get("active")]
        own_db = [t for t in active if (t.get("db_url") or "").strip()]
        _row(_OK, "Tenants registry",
             f"{len(tenants)} total, {len(active)} active, {len(own_db)} with own DB")
        results.append(_OK)
        for t in active:
            kind = "own-DB" if (t.get("db_url") or "").strip() else "control-DB"
            _row(_OK, f"  tenant '{t.get('slug')}'",
                 f"tier={t.get('tier')} {kind}")
    except Exception as e:
        _row(_WARN, "Tenants registry", str(e)[:160])
        results.append(_WARN)


def _check_env(results: list[str]) -> None:
    _section("Configuration (.env)")
    try:
        from src.config.settings import settings
    except Exception as e:
        _row(_ERR, "Settings load", str(e)[:200])
        results.append(_ERR)
        return

    _row(_OK, "Settings loaded", f"ENV={settings.ENV}")

    # Core voice providers — required to talk to Arya.
    required = {
        "DATABASE_URL": settings.DATABASE_URL,
        "SARVAM_API_KEY": settings.SARVAM_API_KEY,
        "GROQ_API_KEY": settings.GROQ_API_KEY,
        "LIVEKIT_URL": settings.LIVEKIT_URL,
        "LIVEKIT_API_KEY": settings.LIVEKIT_API_KEY,
        "LIVEKIT_API_SECRET": settings.LIVEKIT_API_SECRET,
    }
    for key, val in required.items():
        if _is_placeholder(val):
            _row(_ERR, key, "missing / placeholder")
            results.append(_ERR)
        else:
            _row(_OK, key, "set")
            results.append(_OK)

    # Telephony — optional for browser testing, required for phone calls.
    telephony = {
        "PLIVO_AUTH_ID": settings.PLIVO_AUTH_ID,
        "PLIVO_AUTH_TOKEN": settings.PLIVO_AUTH_TOKEN,
        "PLIVO_PHONE_NUMBER": settings.PLIVO_PHONE_NUMBER,
    }
    for key, val in telephony.items():
        if _is_placeholder(val):
            _row(_WARN, key, "not set (only needed for real phone calls)")
        else:
            _row(_OK, key, "set")

    # Production secret hygiene.
    if settings.ENV == "production":
        if _is_placeholder(settings.DASHBOARD_JWT_SECRET):
            _row(_ERR, "DASHBOARD_JWT_SECRET", "weak/default in production")
            results.append(_ERR)
        if _is_placeholder(settings.DASHBOARD_ADMIN_PASSWORD) or \
                len(settings.DASHBOARD_ADMIN_PASSWORD) < 12:
            _row(_ERR, "DASHBOARD_ADMIN_PASSWORD", "weak/default in production")
            results.append(_ERR)
        if settings.CORS_ORIGINS.strip() == "*":
            _row(_WARN, "CORS_ORIGINS", "wildcard in production — lock to your origins")


def _check_imports(results: list[str]) -> None:
    _section("Code imports")
    modules = [
        "src.main",
        "livekit_agent",
        "src.services.scheduler",
        "src.tenancy.registry",
        "src.tenancy.features",
        "src.tenancy.pools",
    ]
    for m in modules:
        try:
            __import__(m)
            _row(_OK, m)
            results.append(_OK)
        except Exception as e:
            _row(_ERR, m, str(e)[:160])
            results.append(_ERR)


async def _check_providers(results: list[str]) -> None:
    """Light live reachability pings — skipped if keys absent."""
    _section("Provider reachability (live ping)")
    try:
        from src.config.settings import settings
        import httpx
    except Exception as e:
        _row(_WARN, "httpx unavailable", str(e)[:120])
        return

    async with httpx.AsyncClient(timeout=8) as client:
        # Groq
        if not _is_placeholder(settings.GROQ_API_KEY):
            try:
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                )
                if r.status_code == 200:
                    _row(_OK, "Groq API", "authenticated")
                    results.append(_OK)
                else:
                    _row(_ERR, "Groq API", f"HTTP {r.status_code}")
                    results.append(_ERR)
            except Exception as e:
                _row(_ERR, "Groq API", str(e)[:120])
                results.append(_ERR)
        else:
            _row(_WARN, "Groq API", "skipped (no key)")

        # Sarvam — hitting models/health is enough to confirm key+network.
        if not _is_placeholder(settings.SARVAM_API_KEY):
            try:
                r = await client.get(
                    "https://api.sarvam.ai/",
                    headers={"api-subscription-key": settings.SARVAM_API_KEY},
                )
                # Any HTTP response (even 404) proves DNS + TLS + reachability.
                _row(_OK, "Sarvam API", f"reachable (HTTP {r.status_code})")
                results.append(_OK)
            except Exception as e:
                _row(_ERR, "Sarvam API", str(e)[:120])
                results.append(_ERR)
        else:
            _row(_WARN, "Sarvam API", "skipped (no key)")


def _check_python(results: list[str]) -> None:
    _section("Runtime")
    major, minor = sys.version_info[:2]
    if (3, 10) <= (major, minor) < (3, 13):
        _row(_OK, "Python version", f"{major}.{minor}")
        results.append(_OK)
    else:
        _row(_WARN, "Python version", f"{major}.{minor} (tested on 3.11)")
    _row(_OK, "Platform", f"{sys.platform}")


async def _run() -> int:
    _emit("=" * 64)
    _emit("  Arteq Hospital Voice Agent — diagnostic")
    _emit("=" * 64)

    results: list[str] = []
    _check_python(results)
    _check_env(results)
    _check_imports(results)
    await _check_db(results)
    await _check_providers(results)

    fails = results.count(_ERR)
    _section("Summary")
    _emit(f"  PASS: {results.count(_OK)}   FAIL: {fails}")
    if fails == 0:
        _emit("\n  All critical checks passed. Ready to test the agent.")
    else:
        _emit("\n  Some checks FAILED. Fix the [FAIL] items above, then re-run:")
        _emit("     python run.py doctor")

    log_path = ROOT / "arteq-diagnostic.log"
    try:
        log_path.write_text(_buf.getvalue(), encoding="utf-8")
        _emit(f"\n  Full report written to: {log_path}")
        _emit("  Copy that file's contents when reporting an issue.")
    except Exception:
        pass

    # Close pools inside this same loop so SSL transports shut down cleanly.
    try:
        from src.db.queries import close_pool
        await close_pool()
    except Exception:
        pass

    return 1 if fails else 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
