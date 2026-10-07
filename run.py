"""AUTO runner: install (uv, global) -> fetch+check models -> start proxy.

Usage:
  python run.py              # full auto: install + fetch/check + serve
  python run.py --no-check   # skip model probing (fast start)
  python run.py --fetch-only # only fetch+check, don't start server
  python run.py --list       # show verified models and exit
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = os.getenv("PROXY_PORT", "6446")

DEPS = ["fastapi", "uvicorn", "httpx"]


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd)


def ensure_deps_global() -> None:
    """Install into the GLOBAL python env using uv (astral)."""
    try:
        subprocess.check_call(["uv", "--version"])
    except Exception:
        print("ERROR: 'uv' not found. Install from https://docs.astral.sh/uv/ "
              "e.g.  powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"")
        sys.exit(1)
    # --system = global lib, as requested (no venv).
    # C:\PythonXX needs admin, so fall back to --user (still global, no venv).
    code = run(["uv", "pip", "install", "--system", *DEPS])
    if code != 0:
        print("uv --system failed (likely no admin), trying user-global install...")
        code = run(["uv", "pip", "install", "--user", *DEPS])
    if code != 0:
        print("uv install failed, trying pip fallback...")
        run([sys.executable, "-m", "pip", "install", "--user", *DEPS])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--skip-install", action="store_true")
    args = ap.parse_args()

    if not args.skip_install:
        ensure_deps_global()

    # fetch + check (this is the auto-update + per-model output verification)
    fetch_cmd = [sys.executable, os.path.join(BASE_DIR, "fetch_models.py")]
    if args.no_check:
        fetch_cmd.append("--no-check")
    if args.list:
        fetch_cmd.append("--list")
        sys.exit(run(fetch_cmd))
    if run(fetch_cmd) != 0:
        print("WARNING: fetch_models failed, continuing with cache/fallback")

    if args.fetch_only:
        return

    # start server (global uvicorn, no venv)
    os.environ.setdefault("PROXY_PORT", PORT)
    print(f"Starting proxy on http://localhost:{PORT} ...")
    sys.exit(run([sys.executable, os.path.join(BASE_DIR, "server.py")]))


if __name__ == "__main__":
    main()
