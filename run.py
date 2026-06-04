#!/usr/bin/env python3
"""
Arteq Hospital Voice Agent — one-command cross-platform launcher.

Works the same on Windows, macOS and Linux with only the standard library.
It will, in order:
  1. verify the Python version,
  2. create a local virtual environment (.venv) if missing,
  3. install/upgrade dependencies from requirements.txt,
  4. create a .env from .env.example if missing and generate strong secrets,
  5. start the FastAPI web server (serves the browser voice client at /talk),
  6. optionally start the LiveKit agent worker (so Arya can actually answer),
  7. open your browser at the "talk to the agent" page.

Usage:
    python run.py                 # set up + run web server, open browser
    python run.py --with-agent    # also run the LiveKit agent worker (talk end-to-end)
    python run.py --no-browser    # don't auto-open the browser
    python run.py --no-install    # skip dependency install (fast restart)
    python run.py --port 8080     # override the web port
    python run.py --agent-only    # only run the LiveKit agent worker

Shortcuts:  ./start.sh  (macOS/Linux)   start.bat  (Windows)
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
IS_WINDOWS = os.name == "nt"


# ── pretty output ──────────────────────────────────────────────────────────────

def _supports_color() -> bool:
    return sys.stdout.isatty() and not IS_WINDOWS or os.environ.get("FORCE_COLOR")


def say(msg: str, kind: str = "info") -> None:
    icons = {"info": "→", "ok": "✓", "warn": "!", "err": "✗", "step": "▶"}
    icon = icons.get(kind, "→")
    print(f"  {icon} {msg}", flush=True)


def banner() -> None:
    print("\n" + "═" * 60)
    print("   Arteq Hospital Voice Agent — local launcher")
    print("═" * 60 + "\n")


# ── venv helpers ─────────────────────────────────────────────────────────────

def venv_python() -> Path:
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def in_target_venv() -> bool:
    """True if we are already running inside the project's .venv."""
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except Exception:
        return False


def check_python() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        say(f"Python {major}.{minor} detected. Python 3.10–3.12 is required.", "err")
        sys.exit(1)
    if (major, minor) >= (3, 13):
        say(f"Python {major}.{minor} detected. This project is tested on 3.11; "
            "some native wheels (pydantic-core, asyncpg) may be missing on 3.13+.", "warn")


def ensure_venv() -> None:
    if VENV_DIR.exists() and venv_python().exists():
        say(".venv already present", "ok")
        return
    say("Creating virtual environment (.venv)…", "step")
    venv.EnvBuilder(with_pip=True, upgrade_deps=False).create(VENV_DIR)
    say("Virtual environment created", "ok")


def pip_install() -> None:
    py = str(venv_python())
    say("Upgrading pip…", "step")
    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], check=False)
    say("Installing dependencies (this can take a few minutes the first time)…", "step")
    rc = subprocess.run([py, "-m", "pip", "install", "-r", str(REQUIREMENTS)]).returncode
    if rc != 0:
        say("Dependency install failed. See the pip output above.", "err")
        sys.exit(rc)
    say("Dependencies installed", "ok")


# ── .env bootstrap ───────────────────────────────────────────────────────────

def ensure_env() -> None:
    if ENV_FILE.exists():
        say(".env already present", "ok")
        return
    if not ENV_EXAMPLE.exists():
        say(".env and .env.example are both missing — cannot bootstrap config.", "err")
        sys.exit(1)
    say("No .env found — creating one from .env.example with generated secrets…", "step")
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    text = _set_env_value(text, "DASHBOARD_JWT_SECRET", secrets.token_hex(32))
    text = _set_env_value(text, "INTERNAL_API_KEY", secrets.token_hex(32))
    text = _set_env_value(text, "DASHBOARD_ADMIN_PASSWORD", "arteqadmin" + secrets.token_hex(4))
    ENV_FILE.write_text(text, encoding="utf-8")
    say(".env created. Add your SARVAM/GROQ/LIVEKIT keys to talk to the agent.", "ok")
    say(f"   (file: {ENV_FILE})", "info")


def _set_env_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    out, found = [], False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") and not stripped.startswith("#"):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def read_env_port(default: int = 8000) -> int:
    if not ENV_FILE.exists():
        return default
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PORT=") and not line.startswith("#"):
                return int(line.split("=", 1)[1].split("#")[0].strip())
    except Exception:
        pass
    return default


def env_has_voice_keys() -> bool:
    if not ENV_FILE.exists():
        return False
    needed = {"SARVAM_API_KEY", "GROQ_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
    placeholders = ("", "your_", "wss://your-project")
    have = set()
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        v = v.split("#")[0].strip()
        if k in needed and v and not any(v.startswith(p) for p in placeholders if p):
            have.add(k)
    return needed.issubset(have)


# ── process management ─────────────────────────────────────────────────────────

def open_browser_later(url: str, delay: float = 3.0) -> None:
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def run_processes(args: argparse.Namespace) -> int:
    py = str(venv_python())
    port = args.port or read_env_port()
    procs: list[tuple[str, subprocess.Popen]] = []

    web_cmd = [py, "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", str(port)]
    if args.reload:
        web_cmd.append("--reload")
    agent_cmd = [py, "livekit_agent.py", "dev"]

    try:
        if not args.agent_only:
            say(f"Starting web server on http://localhost:{port}", "step")
            procs.append(("web", subprocess.Popen(web_cmd, cwd=str(ROOT))))

        if args.with_agent or args.agent_only:
            if not env_has_voice_keys():
                say("Agent worker requested but SARVAM/GROQ/LIVEKIT keys are not set in .env.", "warn")
                say("The worker will start but cannot answer until keys are added.", "warn")
            say("Starting LiveKit agent worker (Arya)…", "step")
            procs.append(("agent", subprocess.Popen(agent_cmd, cwd=str(ROOT))))

        if not args.agent_only and not args.no_browser:
            url = f"http://localhost:{port}/talk"
            open_browser_later(url)
            say(f"Opening {url} in your browser…", "info")

        print("\n" + "─" * 60)
        say("Running. Press Ctrl+C to stop.", "ok")
        if not args.with_agent and not args.agent_only:
            say("Tip: add --with-agent to also run Arya so the agent answers.", "info")
        print("─" * 60 + "\n")

        # Wait until any child exits or the user interrupts.
        while True:
            for name, p in procs:
                rc = p.poll()
                if rc is not None:
                    say(f"'{name}' process exited (code {rc}). Shutting down.", "warn")
                    return rc
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        say("Stopping…", "step")
        return 0
    finally:
        _terminate(procs)


def _terminate(procs) -> None:
    for name, p in procs:
        if p.poll() is None:
            try:
                if IS_WINDOWS:
                    p.terminate()
                else:
                    p.send_signal(signal.SIGINT)
            except Exception:
                pass
    deadline = time.time() + 8
    for name, p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


# ── main ─────────────────────────────────────────────────────────────────────

def run_doctor() -> int:
    """Run the self-diagnostic inside the venv and return its exit code."""
    py = str(venv_python())
    return subprocess.run([py, str(ROOT / "tools" / "doctor.py")], cwd=str(ROOT)).returncode


def run_smoke_call(extra: list[str]) -> int:
    """Run the headless live-voice smoke-call inside the venv."""
    py = str(venv_python())
    return subprocess.run(
        [py, str(ROOT / "tools" / "smoke_call.py")] + extra, cwd=str(ROOT)
    ).returncode


def main() -> int:
    # Positional subcommands (`doctor`, `smoke-call`) — setup the venv first,
    # then dispatch. Survive the venv re-exec via env flags so argparse never
    # sees the positional token.
    def _take_subcommand(name: str, env_flag: str) -> bool:
        active = (len(sys.argv) > 1 and sys.argv[1] == name) \
            or os.environ.get(env_flag) == "1"
        if len(sys.argv) > 1 and sys.argv[1] == name:
            sys.argv.pop(1)
        if active:
            os.environ[env_flag] = "1"
        return active

    is_doctor = _take_subcommand("doctor", "ARTEQ_DOCTOR")
    is_smoke = _take_subcommand("smoke-call", "ARTEQ_SMOKECALL")

    parser = argparse.ArgumentParser(description="Arteq Hospital Voice Agent launcher")
    parser.add_argument("--with-agent", "-a", action="store_true", help="also run the LiveKit agent worker")
    parser.add_argument("--agent-only", action="store_true", help="run only the LiveKit agent worker")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--no-install", action="store_true", help="skip dependency install")
    parser.add_argument("--reload", action="store_true", help="run uvicorn with --reload (dev)")
    parser.add_argument("--port", type=int, default=0, help="web server port (default: from .env or 8000)")
    args = parser.parse_args()

    banner()
    check_python()

    # Setup phase (always run by the system interpreter, not the venv one).
    if not in_target_venv():
        ensure_venv()
        if not args.no_install:
            pip_install()
        ensure_env()
        # Re-exec inside the venv so imports resolve against installed deps.
        say("Launching inside the virtual environment…\n", "step")
        py = str(venv_python())
        os.execv(py, [py, str(ROOT / "run.py")] + sys.argv[1:])
        return 0  # unreachable

    # We are now inside .venv.
    ensure_env()
    if is_doctor:
        return run_doctor()
    if is_smoke:
        return run_smoke_call(sys.argv[1:])
    return run_processes(args)


if __name__ == "__main__":
    sys.exit(main())
