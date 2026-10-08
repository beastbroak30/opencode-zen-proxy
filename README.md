# opencode-zen-proxy

[![daily-model-status](https://github.com/beastbroak30/opencode-zen-proxy/actions/workflows/daily-models.yml/badge.svg)](https://github.com/beastbroak30/opencode-zen-proxy/actions/workflows/daily-models.yml)

> Free LLMs via OpenCode Zen, exposed as a standard OpenAI / Anthropic API.
> Point any OpenAI-compatible client at this proxy and use OpenCode's
> free-tier models (`mimo-v2.6-flash-free`, `muse-spark-1.3-contributor-free`,
> `big-pickle`, …) with no API key, streaming, tool calls, and reasoning.

**Keywords:** opencode, opencode zen, zen api, free llm, free models,
openai-compatible proxy, anthropic-compatible proxy, local llm gateway,
`*-free` models, mimo, muse-spark, big-pickle, space-bunny, nemotron,
model router, llm proxy, self-hosted ai gateway.

> **Disclaimer — educational purposes only.** Demonstrates how OpenCode's
> free-tier models can be accessed through a standard local API. Not
> affiliated with OpenCode. Free models, limits, and terms change often;
> respect OpenCode's terms of service.

## What this is

A tiny Python gateway that turns OpenCode's free models into a drop-in
OpenAI endpoint (`POST /v1/chat/completions`, `GET /v1/models`) plus an
Anthropic endpoint (`POST /v1/messages`):

- **Finds free models automatically** — live list from `opencode.ai/zen/v1/models`,
  zero-cost entries from `models.dev`, upstream repo list.
- **Health-checks every model** before serving; unreachable or rejected models
  are auto-removed and never advertised again.
- **Routes through `opencode serve`** (recommended): the real backend supplies
  the key, session headers, and correct per-model protocol
  (`/chat/completions` vs `/responses` vs `/systemone`).
- **Streams tokens, tool calls, and thinking** in both formats
  (`reasoning_content` / `thinking_delta`).

## Quickstart (macOS / Linux / Windows)

```bash
python setup.py            # install deps → start opencode serve → probe → serve on :6446
python setup.py --no-check # skip probing, use cache
```

Then call it like OpenAI — no API key needed:

```bash
curl http://localhost:6446/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mimo-v2.6-flash-free",
       "messages":[{"role":"user","content":"Hello"}],
       "stream":true}'
```

Works from other devices on your LAN too — the proxy binds `0.0.0.0:6446`
(e.g. `http://192.168.1.39:6446/v1`). Set `REQUIRE_AUTH=1` before exposing it
further.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/chat/completions` | OpenAI format, `stream` supported, `reasoning_content` deltas |
| `POST` | `/v1/messages` | Anthropic format, SSE + `thinking` blocks |
| `GET` | `/v1/models` | Only currently-working free models |
| `GET` | `/health` | Status + disabled-model list |
| `POST` | `/v1/refresh` | Re-fetch and re-probe the catalog |

## Models (verified 2026-10-07)

`big-pickle`, `exo-free`, `fledge-alpha-free`, `ling-3.1-flash-free`,
`longcat-2.5-preview-free`, `mimo-v2.6-flash-free`,
`muse-spark-1.2-contributor-free`, `muse-spark-1.3-contributor-free`,
`nemotron-3-ultra-free`, `nemotron-3.5-lightning-free`, `space-bunny-free`.

The free roster rotates; dead models are dropped automatically and new promos
are picked up on refresh.

## Configuration

`PROXY_PORT` (6446) · `KEYS_FILE` · `REFRESH_MINUTES` (30) · `SKIP_CHECK`
· `REQUIRE_AUTH` (0|1) · `OPENCODE_SERVER_URL` (default `http://127.0.0.1:4096`)
· `python server.py --no-check --port 6446`.

## Privacy

- No keys/tokens committed (`api-keys.json`, logs git-ignored).
- Upstream credentials come from the OS credential store or `OPENCODE_API_KEY`.
- Outbound traffic only to `opencode.ai`, `models.dev`, GitHub raw.

Built with help from OpenCode itself.
Inspired by https://github.com/bigdata2211it-web/opencode-free-proxy

## Live status

<!-- LIVE-STATUS-START -->
_Last checked: 2026-10-08 08:46 UTC — **0/37 live**_

| Model | Status | Note |
|---|---|---|
| `big-pickle` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `deepseek-v4-flash-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `exo-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `fledge-alpha-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `jev-1.13-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `ling-3.0-flash-fin-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `ling-3.1-flash-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `longcat-2.5-preview-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `mimo-v2.5-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `mimo-v2.6-flash-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `muse-spark-1.2-contributor-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `muse-spark-1.3-contributor-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `nemotron-3-ultra-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `nemotron-3.5-lightning-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `space-bunny-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `glm-4.7-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `glm-5-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `hy3-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `hy3-preview-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `kimi-k2.5-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `laguna-s-2.1-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `ling-2.6-flash-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `ling-3.0-flash-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `ling-3.0-tiny-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `longcat-2.0-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `mimo-v2-flash-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `mimo-v2-omni-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `mimo-v2-pro-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `minimax-m2.1-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `minimax-m2.5-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `minimax-m3-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `nemotron-3-super-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `north-mini-code-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `qwen3.6-plus-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `ring-2.6-1t-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `trinity-large-preview-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
| `x-preview-f-free` | 🔴 | {"type":"error","error":{"type":"ModelError","message":"Model  is not supported" |
<!-- LIVE-STATUS-END -->
