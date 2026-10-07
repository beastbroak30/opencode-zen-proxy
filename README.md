# opencode-free-proxy-py

> **Disclaimer — educational purposes only.** This project exists to demonstrate
> how OpenCode's free-tier (`*-free`) models can be accessed through a standard,
> OpenAI/Anthropic-compatible local API. It is **not** affiliated with, endorsed
> by, or sponsored by OpenCode. Free models and their terms may change or be
> rate-limited at any time; use the official OpenCode client where it matters.
> Respect OpenCode's terms of service.

Python port of https://github.com/bigdata2211it-web/opencode-free-proxy
with **auto-fetch + per-model health check**, plus optional routing through a
headless `opencode serve` backend (recommended).

On every start:

1. **Fetches** free-model candidates from the web:
   - LIVE: `GET https://opencode.ai/zen/v1/models` -> ids ending `-free` + `big-pickle`
   - `GET https://models.dev/api.json` -> `opencode.models` with cost 0/0
   - GitHub upstream `server.mjs` MODELS array (picks up repo updates)
2. **Probes each model** with a tiny request via Zen
   (same `x-opencode-*` headers, correct endpoint per model:
   `/chat/completions`, `/responses` for `muse-spark*`, `/systemone` for `jev-*`).
3. **Serves only working models**, refreshes in background every 30 min.

## Quick setup

**Windows (PowerShell):**
```powershell
.\setup.ps1
```

**macOS / Linux:**
```bash
chmod +x setup.sh && ./setup.sh
```

The setup script installs python deps, starts `opencode serve` in the
background (port 4096) if it isn't running, probes free models, and starts
the proxy on port 6446.

Or manually:

```powershell
python run.py          # installs deps (uv/pip), fetches, probes, serves
python run.py --no-check
python run.py --fetch-only
python run.py --list
```

## Use

Server: `http://localhost:6446`.

No API key is required on incoming calls by default (the proxy injects its
upstream credentials). Set `REQUIRE_AUTH=1` to turn auth back on; keys live in
`api-keys.json` (auto-generated, git-ignored).

```powershell
# OpenAI
Invoke-RestMethod http://localhost:6446/v1/models
$body = @{model="space-bunny-free"; messages=@(@{role="user";content="Hello"}); stream=$false} | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:6446/v1/chat/completions -Method Post `
  -ContentType "application/json" -Body $body

# Anthropic: POST /v1/messages
# Health: GET /health | Manual refresh: POST /v1/refresh
```

Stream reasoning is returned as `delta.reasoning_content` (OpenAI format) and
`thinking_delta` (Anthropic format).

Env: `PROXY_PORT=6446`, `KEYS_FILE=./api-keys.json`, `REFRESH_MINUTES=30`,
`SKIP_CHECK=1`, `REQUIRE_AUTH=0|1`, `OPENCODE_SERVER_URL` (default
`http://127.0.0.1:4096`).

## Status 2026-10-07 (verified live)

Probed 37 candidates. Working: `big-pickle`, `exo-free`, `fledge-alpha-free`,
`ling-3.1-flash-free`, `longcat-2.5-preview-free`, `mimo-v2.6-flash-free`,
`muse-spark-1.2/1.3-contributor-free`, `nemotron-3-ultra-free`,
`nemotron-3.5-lightning-free`, `space-bunny-free`.
The rest are deprecated/unavailable upstream. This rotates often — the
auto-check picks up new free promos automatically.

Built with help from OpenCode itself.
