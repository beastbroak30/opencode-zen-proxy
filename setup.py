"""Universal cross-platform setup: macOS / Linux / Windows.

One command sets everything up:
    python setup.py

Steps:
  1. install python dependencies (fastapi, uvicorn, httpx)
  2. start `opencode serve` on port 4096 if it isn't running (if opencode is installed)
  3. fetch + probe free models (fetch_models.py)
  4. start the proxy on port 6446 (server.py)

Options:
  --no-check     skip per-model probing (use cache)
  --fetch-only   only update models.json, don't start server
  --no-serve     don't auto-start opencode serve
  --skip-install skip pip install
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("PROXY_PORT", "6446"))
OPENCODE_PORT = int(os.getenv("OPENCODE_PORT", "4096"))
DEPS = ["fastapi", "uvicorn", "httpx"]


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def install_deps() -> None:
    try:
        import fastapi, uvicorn, httpx  # noqa: F401
        print("dependencies already installed")
        return
    except ImportError:
        pass
    code = run([sys.executable, "-m", "pip", "install", *DEPS])
    if code != 0:
        code = run([sys.executable, "-m", "pip", "install", "--user", *DEPS])
    if code != 0:
        print("ERROR: could not install dependencies (try pipx/uv)")
        sys.exit(1)


def ensure_opencode() -> None:
    if port_open(OPENCODE_PORT):
        print(f"opencode serve already on :{OPENCODE_PORT}")
        return
    exe = shutil.which("opencode")
    if not exe:
        print("opencode not found on PATH — free-tier routing needs it (https://opencode.ai)")
        return
    print("starting opencode serve in background...")
    log = open(os.path.join(BASE_DIR, "opencode-serve.log"), "ab")
    kwargs = dict(stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([exe, "serve", "--port", str(OPENCODE_PORT), "--hostname", "127.0.0.1"], **kwargs)
    for _ in range(30):
        if port_open(OPENCODE_PORT):
            print(f"opencode serve up on :{OPENCODE_PORT}")
            return
        time.sleep(1)
    print("WARNING: opencode serve did not come up in 30s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--no-serve", action="store_true")
    ap.add_argument("--skip-install", action="store_true")
    args = ap.parse_args()

    if not args.skip_install:
        install_deps()

    if not args.no_serve:
        ensure_opencode()

    fetch = [sys.executable, os.path.join(BASE_DIR, "fetch_models.py")]
    if args.no_check:
        fetch.append("--no-check")
    run(fetch)

    if args.fetch_only:
        return

    os.environ.setdefault("PROXY_PORT", str(PORT))
    print(f"Starting proxy on http://0.0.0.0:{PORT} ...")
    try:
        sys.exit(run([sys.executable, os.path.join(BASE_DIR, "server.py")]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
