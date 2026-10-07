#!/usr/bin/env bash
# opencode-zen-proxy setup for macOS / Linux
set -e
cd "$(dirname "$0")"

echo "== checking python =="
PY=$(command -v python3 || command -v python)
[ -z "$PY" ] && { echo "Install python3 first"; exit 1; }

echo "== installing dependencies =="
$PY -m pip install --user fastapi uvicorn httpx || $PY -m pip install fastapi uvicorn httpx

echo "== checking opencode =="
if command -v opencode >/dev/null 2>&1; then
  if ! curl -s -m 3 http://localhost:4096/global/health | grep -q healthy; then
    echo "== starting opencode serve in background =="
    nohup opencode serve --port 4096 --hostname 127.0.0.1 > opencode-serve.log 2>&1 &
    sleep 3
  fi
else
  echo "opencode not found on PATH — free-tier routing needs it (https://opencode.ai)"
fi

echo "== fetching + verifying free models =="
$PY fetch_models.py || true

echo "== starting proxy on :6446 =="
exec $PY server.py
