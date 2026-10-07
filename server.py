"""Proxy for OpenCode free-tier models (inspired by opencode-free-proxy server.mjs) with auto-fetch + health-check.

- OpenAI compat:  POST /v1/chat/completions, GET /v1/models
- Anthropic:      POST /v1/messages
- Health:         GET /health
- Auto-fetch free models from web (zen live + models.dev + github upstream)
  at startup + background refresh every REFRESH_MINUTES.
- Before serving, each candidate model is probed; only working ones are exposed.
  (Same behaviour as Node version otherwise: same auth headers, sessions, formats.)

Run:  python run.py   (auto: install -> fetch+check -> start)
      python server.py
Env:  PROXY_PORT (default 6446), KEYS_FILE (default ./api-keys.json),
      REFRESH_MINUTES (default 30), SKIP_CHECK=1 to skip startup probe
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from fetch_models import (
    FALLBACK_MODELS,
    load_cached,
    refresh_models,
    upstream_path_for,
    zen_headers,
    ZEN_TOOLS,
    ZEN_TOOLS_RESPONSES,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("PROXY_PORT", "6446"))
KEYS_FILE = os.getenv("KEYS_FILE", os.path.join(BASE_DIR, "api-keys.json"))
OC_VERSION = "1.18.35"
PROXY_VERSION = "10-py"
REFRESH_MINUTES = int(os.getenv("REFRESH_MINUTES", "30"))

# ---- state ----
MODELS: list[str] = load_cached()
MODELS_UPDATED_AT: str = ""
api_keys: dict[str, str] = {}
user_sessions: dict[str, dict] = {}


_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def oc_id(prefix: str) -> str:
    if prefix == "ses":
        ts = format(int(time.time() * 1000), "x")[:12].ljust(12, "0")
        rnd = "".join(secrets.choice(_ALNUM) for _ in range(14))
        return f"ses_{ts}{rnd}"
    if prefix == "msg":
        return "msg_" + "".join(secrets.choice(_ALNUM) for _ in range(26))
    ts = format(int(time.time() * 1000), "x")
    rnd = base64.urlsafe_b64encode(secrets.token_bytes(12)).decode().rstrip("=")[:16]
    return f"{prefix}_{ts}{rnd}"


def load_keys() -> None:
    global api_keys
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            api_keys = json.load(f)
    except Exception:
        api_keys = {}
    if not api_keys:
        api_keys = {
            "admin": "oc-" + secrets.token_hex(20),
            "user-default": "oc-" + secrets.token_hex(20),
        }
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(api_keys, f, indent=2)
        print(f"[INIT] Generated new API keys -> {KEYS_FILE}")


def check_auth(req: Request) -> str | None:
    if os.getenv("REQUIRE_AUTH", "0") != "1":
        hdr = req.headers.get("authorization") or req.headers.get("x-api-key") or ""
        tok = hdr[7:] if hdr.startswith("Bearer ") else hdr
        for name, key in api_keys.items():
            if tok and tok == key:
                return name
        return "default"
    hdr = req.headers.get("authorization") or req.headers.get("x-api-key") or ""
    tok = hdr[7:] if hdr.startswith("Bearer ") else hdr
    for name, key in api_keys.items():
        if tok == key:
            return name
    return None


def get_session(user: str) -> str:
    now = time.time() * 1000
    s = user_sessions.get(user)
    if not s or now - s["ts"] > 30 * 60 * 1000:
        s = {"id": oc_id("ses"), "ts": now}
        user_sessions[user] = s
    return s["id"]


def boot_refresh() -> None:
    """Fetch + probe before serving (unless SKIP_CHECK=1)."""
    global MODELS, MODELS_UPDATED_AT
    load_keys()
    if os.getenv("SKIP_CHECK") == "1":
        MODELS = load_cached()
        print(f"[BOOT] SKIP_CHECK=1, using cached: {MODELS}")
        return
    try:
        MODELS = refresh_models(do_check=True)
        MODELS_UPDATED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    except Exception as e:
        print(f"[BOOT] refresh failed ({e}), using cached/fallback")
        MODELS = load_cached() or list(FALLBACK_MODELS)


async def background_refresher() -> None:
    global MODELS, MODELS_UPDATED_AT
    while True:
        await asyncio.sleep(REFRESH_MINUTES * 60)
        try:
            loop = asyncio.get_running_loop()
            verified = await loop.run_in_executor(None, lambda: refresh_models(do_check=True))
            MODELS = verified
            MODELS_UPDATED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(f"[REFRESH] models updated: {MODELS}")
        except Exception as e:
            print(f"[REFRESH] failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # boot refresh is sync (probes + asyncio.run inside); run it in a thread so
    # it doesn't conflict with uvicorn's running event loop
    await asyncio.get_running_loop().run_in_executor(None, boot_refresh)
    task = asyncio.create_task(background_refresher())
    print(f"OpenCode Free Proxy {PROXY_VERSION} on http://0.0.0.0:{PORT}")
    print("  OpenAI:    POST /v1/chat/completions")
    print("  Anthropic: POST /v1/messages")
    print("  Models:    GET  /v1/models")
    print("  Health:    GET  /health")
    print(f"  Models: {', '.join(MODELS)}")
    for name, key in api_keys.items():
        print(f"  {name:<15} {key}")
    yield
    task.cancel()


app = FastAPI(title="opencode-zen-proxy", version=PROXY_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


# ---------- Anthropic <-> OpenAI conversion (port of server.mjs) ----------

def anthropic_to_openai(body: dict) -> tuple[list, list | None]:
    messages: list = []
    if body.get("system"):
        sys = body["system"]
        if isinstance(sys, str):
            s = sys
        elif isinstance(sys, list):
            s = "\n".join(b.get("text", "") for b in sys if isinstance(b, dict))
        else:
            s = ""
        if s:
            messages.append({"role": "system", "content": s})
    for msg in body.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            messages.append({"role": msg.get("role"), "content": content})
        elif isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            if tool_uses and msg.get("role") == "assistant":
                messages.append({
                    "role": "assistant", "content": text or None,
                    "tool_calls": [{
                        "id": t.get("id"), "type": "function",
                        "function": {"name": t.get("name"),
                                     "arguments": json.dumps(t.get("input") or {})},
                    } for t in tool_uses],
                })
            elif any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        c = b.get("content")
                        if isinstance(c, str):
                            rt = c
                        elif isinstance(c, list):
                            rt = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                        else:
                            rt = ""
                        messages.append({"role": "tool",
                                         "tool_call_id": b.get("tool_use_id"), "content": rt})
            else:
                messages.append({"role": msg.get("role"), "content": text})
    tools = [{
        "type": "function",
        "function": {"name": t.get("name"), "description": t.get("description") or "",
                     "parameters": t.get("input_schema") or {}},
    } for t in (body.get("tools") or [])]
    return messages, (tools or None)


def openai_to_anthropic(oai: dict, model: str, input_tokens: int) -> dict:
    choices = (oai.get("choices") or [])
    if not choices:
        return {"id": oc_id("msg"), "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": ""}], "model": model,
                "stop_reason": "end_turn",
                "usage": {"input_tokens": input_tokens or 0, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}
    msg = choices[0].get("message") or {}
    content: list = []
    if msg.get("reasoning_content"):
        content.append({"type": "thinking", "thinking": msg["reasoning_content"]})
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        try:
            inp = json.loads(tc.get("function", {}).get("arguments") or "{}")
        except Exception:
            inp = {}
        content.append({"type": "tool_use", "id": tc.get("id") or oc_id("toolu"),
                        "name": tc.get("function", {}).get("name"), "input": inp})
    if not content:
        content.append({"type": "text", "text": ""})
    fr = choices[0].get("finish_reason")
    stop = "end_turn"
    if fr == "tool_calls":
        stop = "tool_use"
    elif fr == "length":
        stop = "max_tokens"
    usage = oai.get("usage") or {}
    return {"id": oc_id("msg"), "type": "message", "role": "assistant",
            "content": content, "model": model, "stop_reason": stop,
            "usage": {"input_tokens": usage.get("prompt_tokens") or input_tokens or 0,
                      "output_tokens": usage.get("completion_tokens") or 0,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


def responses_to_chat_text(data: dict) -> tuple[str, dict | None]:
    """Extract text from Responses API payload -> (text, usage)."""
    texts: list[str] = []
    for item in data.get("output") or []:
        for part in (item.get("content") or []):
            if isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
    usage = data.get("usage") or {}
    return "".join(texts), usage


# ---------- Zen transport ----------

async def zen_chat_passthrough(model: str, payload: dict, session: str,
                               stream: bool):
    """Yield raw bytes from Zen /chat/completions (or /systemone)."""
    path = upstream_path_for(model)
    url = f"https://opencode.ai{path}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload,
                                 headers=zen_headers(session)) as r:
            if r.status_code == 429:
                detail = (await r.aread()).decode("utf-8", "replace")[:300]
                raise RateLimited(detail)
            async for chunk in r.aiter_bytes():
                yield chunk


class RateLimited(Exception):
    pass


async def zen_chat_full(model: str, payload: dict, session: str) -> httpx.Response:
    """Non-stream client request: still call Zen with stream=True (the free-tier
    gate rejects non-streaming), then collect the SSE into a normal JSON reply."""
    path = upstream_path_for(model)
    url = f"https://opencode.ai{path}"
    payload = dict(payload)
    payload["stream"] = True
    payload.setdefault("tools", ZEN_TOOLS)
    text_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    completion_tokens = 0
    prompt_tokens = 0
    model_id = model
    created = int(time.time())
    finish_reason = "stop"
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload,
                                 headers=zen_headers(session)) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode("utf-8", "replace")
                raise UpstreamError(r.status_code, body[:300])
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue
                if chunk.get("error"):
                    raise UpstreamError(400, str(chunk["error"])[:300])
                created = chunk.get("created", created)
                model_id = chunk.get("model", model_id)
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        text_parts.append(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_calls.setdefault(idx, {"id": tc.get("id"), "type": "function",
                                                           "function": {"name": "", "arguments": ""}})
                        f = tc.get("function") or {}
                        if f.get("name"):
                            slot["function"]["name"] += f["name"]
                        if f.get("arguments"):
                            slot["function"]["arguments"] += f["arguments"]
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                usage = chunk.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        finish_reason = "tool_calls"
    return CollectedResponse(
        chat_response_obj(model_id, message["content"],
                          {"input_tokens": prompt_tokens, "output_tokens": completion_tokens})
        if not tool_calls else
        {"id": oc_id("chatcmpl"), "object": "chat.completion", "created": created,
         "model": model_id, "choices": [{"index": 0, "message": message,
                                          "finish_reason": finish_reason}],
         "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                   "total_tokens": prompt_tokens + completion_tokens}},
        created, model_id, finish_reason, prompt_tokens, completion_tokens, message)


class UpstreamError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class CollectedResponse:
    def __init__(self, obj: dict, created: int, model_id: str, finish_reason: str,
                 prompt_tokens: int, completion_tokens: int, message: dict):
        self.obj = obj
        self.created = created
        self.model_id = model_id
        self.finish_reason = finish_reason
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.message = message

    def json(self) -> dict:
        return self.obj

    @property
    def status_code(self) -> int:
        return 200

    @property
    def text(self) -> str:
        return json.dumps(self.obj)


async def zen_responses_full(model: str, messages: list, session: str,
                             max_tokens: int = 1024) -> tuple[str, dict]:
    """Call /responses for muse-spark models, return (text, usage)."""
    url = "https://opencode.ai/zen/v1/responses"
    # Responses input: keep it simple, pass chat messages as input
    resp_input: list = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        if role == "tool":
            resp_input.append({"type": "function_call_output",
                               "call_id": m.get("tool_call_id"), "output": m.get("content") or ""})
        elif m.get("tool_calls"):
            for tc in m["tool_calls"]:
                resp_input.append({"type": "function_call", "call_id": tc.get("id"),
                                   "name": (tc.get("function") or {}).get("name"),
                                   "arguments": (tc.get("function") or {}).get("arguments", "")})
        else:
            resp_input.append({"role": role, "content": m.get("content") or ""})
    payload = {"model": model, "input": resp_input,
               "max_output_tokens": max_tokens, "stream": True,
               "tools": ZEN_TOOLS_RESPONSES}
    text_parts: list[str] = []
    usage: dict = {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload, headers=zen_headers(session)) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode("utf-8", "replace")
                raise UpstreamError(r.status_code, body[:300])
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except Exception:
                    continue
                if ev.get("type") == "response.output_text.delta":
                    text_parts.append(ev.get("delta", ""))
                if ev.get("type") == "response.completed":
                    resp = ev.get("response") or {}
                    u = resp.get("usage") or {}
                    usage = {"input_tokens": u.get("input_tokens", 0),
                             "output_tokens": u.get("output_tokens", 0)}
                if ev.get("type") == "error" or (ev.get("error")):
                    raise UpstreamError(400, str(ev)[:300])
    return "".join(text_parts), usage


def chat_response_obj(model: str, text: str, usage: dict | None = None,
                      tool_calls: list | None = None, reasoning: str | None = None) -> dict:
    usage = usage or {}
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    finish = "stop"
    if tool_calls:
        msg["tool_calls"] = tool_calls
        finish = "tool_calls"
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"id": oc_id("chatcmpl"), "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage.get("input_tokens", 0),
                      "completion_tokens": usage.get("output_tokens", 0),
                      "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)}}


# ---------- routes ----------

@app.get("/v1/models")
async def list_models():
    return {"object": "list",
            "data": [{"id": m, "object": "model", "created": 1779000000,
                      "owned_by": "opencode-free"} for m in MODELS]}


@app.get("/health")
async def health():
    return {"status": "ok", "version": f"v{PROXY_VERSION}", "models": MODELS,
            "count": len(MODELS), "updated_at": MODELS_UPDATED_AT,
            "endpoints": ["/v1/chat/completions", "/v1/messages", "/v1/models"]}


@app.post("/v1/refresh")
async def refresh(req: Request):
    user = check_auth(req)
    if not user:
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    loop = asyncio.get_running_loop()
    verified = await loop.run_in_executor(None, lambda: refresh_models(do_check=True))
    global MODELS, MODELS_UPDATED_AT
    MODELS = verified
    MODELS_UPDATED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"status": "ok", "models": MODELS}


SERVE_URL = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096")
serve_sessions: dict[str, str] = {}


async def serve_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{SERVE_URL}/global/health")
            return r.status_code == 200 and r.json().get("healthy") is True
    except Exception:
        return False


async def serve_session(user: str) -> str:
    sid = serve_sessions.get(user)
    if sid:
        return sid
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(f"{SERVE_URL}/session", json={})
        sid = r.json()["id"]
    serve_sessions[user] = sid
    return sid


async def serve_chat(user: str, model: str, messages: list) -> tuple[str, dict]:
    """Route through the real opencode backend (opencode serve): it injects the
    correct Zen key + x-opencode-* headers itself, so no key handling here."""
    sid = await serve_session(user)
    last_user = ""
    system_parts = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            system_parts.append(m.get("content") or "")
        elif m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
            last_user = c or ""
    text_in = last_user
    if system_parts:
        text_in = "\n".join(system_parts) + "\n\n" + last_user
    payload = {"model": {"providerID": "opencode", "modelID": model},
               "parts": [{"type": "text", "text": text_in}]}
    async with httpx.AsyncClient(timeout=300.0) as c:
        r = await c.post(f"{SERVE_URL}/session/{sid}/message", json=payload)
        r.raise_for_status()
        data = r.json()
    text = "".join(p.get("text", "") for p in data.get("parts") or []
                   if isinstance(p, dict) and p.get("type") == "text")
    reasoning = "".join(p.get("text", "") for p in data.get("parts") or []
                        if isinstance(p, dict) and p.get("type") == "reasoning")
    tokens = (data.get("info") or {}).get("tokens") or {}
    tool_calls = []
    for p in data.get("parts") or []:
        if isinstance(p, dict) and p.get("type") == "tool":
            st = p.get("state") or {}
            tool_calls.append({"id": p.get("callID") or p.get("id") or oc_id("toolu"),
                               "type": "function",
                               "function": {"name": p.get("tool"),
                                            "arguments": json.dumps(st.get("input") or {})}})
    return text, {"input_tokens": tokens.get("input", 0),
                  "output_tokens": tokens.get("output", 0),
                  "reasoning": reasoning}, tool_calls


async def serve_chat_stream(user: str, model: str, messages: list):
    """Yield OpenAI-style text/tool deltas by tailing the opencode server event bus."""
    sid = await serve_session(user)
    last_user = ""
    system_parts = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            system_parts.append(m.get("content") or "")
        elif m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = "".join(b.get("text", "") for b in c if isinstance(b, dict))
            last_user = c or ""
    text_in = last_user
    if system_parts:
        text_in = "\n".join(system_parts) + "\n\n" + last_user
    payload = {"model": {"providerID": "opencode", "modelID": model},
               "parts": [{"type": "text", "text": text_in}]}
    seen_tools: set = set()
    reasoning_parts: set = set()
    async with httpx.AsyncClient(timeout=None) as c:
        async with c.stream("GET", f"{SERVE_URL}/event") as ev:
            try:
                r = await c.post(f"{SERVE_URL}/session/{sid}/prompt_async", json=payload,
                                 timeout=15.0)
                r.raise_for_status()
            except Exception:
                return
            async for line in ev.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    e = json.loads(line[5:].strip())
                except Exception:
                    continue
                props = e.get("properties") or {}
                if props.get("sessionID") != sid:
                    continue
                t = e.get("type")
                if t == "message.part.delta" and props.get("field") == "text":
                    if props.get("partID") in reasoning_parts:
                        yield {"kind": "reasoning", "text": props.get("delta", "")}
                    else:
                        yield {"kind": "text", "text": props.get("delta", "")}
                elif t == "message.part.updated":
                    part = props.get("part") or {}
                    if part.get("type") == "reasoning":
                        reasoning_parts.add(part.get("id"))
                    if (part.get("type") == "tool" and part.get("id") not in seen_tools
                            and (part.get("state") or {}).get("status") in ("completed", "error")):
                        seen_tools.add(part.get("id"))
                        st = part.get("state") or {}
                        yield {"kind": "tool", "id": part.get("callID") or part.get("id"),
                               "name": part.get("tool"),
                               "arguments": json.dumps(st.get("input") or {})}
                elif t == "session.idle":
                    return


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    user = check_auth(req)
    if not user:
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    body = await req.json()
    model = body.get("model")
    if model not in MODELS:
        return JSONResponse(
            {"error": {"message": f"Unknown model: {model}. Available: {', '.join(MODELS)}"}},
            status_code=400)
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    session = get_session(user)
    print(f"[OAI] {user} {model} {'stream' if stream else 'sync'} msgs={len(messages)}")

    if await serve_available():
        if stream:
            async def gen():
                created = int(time.time()); cid = oc_id("chatcmpl")
                try:
                    async for ev in serve_chat_stream(user, model, messages):
                        if ev["kind"] == "text":
                            yield f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ev["text"]}, "finish_reason": None}]})}\n\n'
                        elif ev["kind"] == "reasoning":
                            yield f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"reasoning_content": ev["text"]}, "finish_reason": None}]})}\n\n'
                        else:
                            yield f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": ev["id"], "type": "function", "function": {"name": ev["name"], "arguments": ev["arguments"]}}]}, "finish_reason": None}]})}\n\n'
                except Exception as e:
                    yield f'data: {json.dumps({"error": {"message": str(e)}})}\n\n'
                yield f'data: {json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        try:
            text, usage, tool_calls = await serve_chat(user, model, messages)
            return JSONResponse(chat_response_obj(model, text,
                                                  {"input_tokens": usage["input_tokens"],
                                                   "output_tokens": usage["output_tokens"]},
                                                  tool_calls or None,
                                                  usage.get("reasoning") or None))
        except Exception as e:
            print(f"[serve] fallback to direct Zen: {e}")

    # muse-spark models go through /responses (translated)
    if upstream_path_for(model) == "/zen/v1/responses":
        max_t = body.get("max_tokens") or 1024
        try:
            text, usage = await zen_responses_full(model, messages, session, max_t)
        except UpstreamError as e:
            return JSONResponse({"error": {"message": f"Upstream {e.status}: {e.detail}"}},
                                status_code=502)
        except Exception as e:
            return JSONResponse({"error": {"message": f"Upstream error: {e}"}}, status_code=502)
        if stream:  # fake-stream for SSE clients
            async def gen():
                yield f'data: {json.dumps({"id": oc_id("chatcmpl"), "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]})}\n\n'
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse(chat_response_obj(model, text, usage))

    payload = {"model": model, "messages": messages, "stream": stream}
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    if stream:
        async def gen():
            try:
                payload["stream"] = True
                payload.setdefault("tools", ZEN_TOOLS)
                async for chunk in zen_chat_passthrough(model, payload, session, True):
                    yield chunk
            except RateLimited as e:
                yield json.dumps({"error": {"message": str(e) + " (free model rate limit)",
                                             "type": "rate_limit_error"}}).encode()
        return StreamingResponse(gen(), media_type="text/event-stream")
    try:
        r = await zen_chat_full(model, payload, session)
    except UpstreamError as e:
        if e.status == 429:
            return JSONResponse({"error": {"message": "Rate limit exceeded (free model rate limit)",
                                            "type": "rate_limit_error"}}, status_code=429)
        return JSONResponse({"error": {"message": f"Upstream {e.status}: {e.detail}"}},
                            status_code=502)
    except Exception as e:
        return JSONResponse({"error": {"message": f"Upstream error: {e}"}}, status_code=502)
    return JSONResponse(r.json())


@app.post("/v1/messages")
async def anthropic_messages(req: Request):
    user = check_auth(req)
    if not user:
        return JSONResponse({"type": "error",
                             "error": {"type": "authentication_error",
                                       "message": "Invalid API key"}}, status_code=401)
    body = await req.json()
    model = body.get("model")
    if model not in MODELS:
        return JSONResponse({"type": "error",
                             "error": {"type": "invalid_request_error",
                                       "message": f"Unknown model: {model}. "
                                                  f"Available: {', '.join(MODELS)}"}},
                            status_code=400)
    stream = bool(body.get("stream"))
    max_t = body.get("max_tokens") or 1024
    messages, tools = anthropic_to_openai(body)
    input_tokens = len(json.dumps(messages)) // 4
    session = get_session(user)
    print(f"[ANT] {user} {model} {'stream' if stream else 'sync'} msgs={len(messages)}")

    if upstream_path_for(model) == "/zen/v1/responses":
        if await serve_available():
            try:
                text, usage, tool_calls = await serve_chat(user, model, messages)
                oai = chat_response_obj(model, text, usage, tool_calls or None,
                                        usage.get("reasoning") or None)
                return JSONResponse(openai_to_anthropic(oai, model, input_tokens))
            except Exception as e:
                print(f"[serve] anthropic fallback: {e}")
        try:
            text, usage = await zen_responses_full(model, messages, session, max_t)
        except Exception as e:
            return JSONResponse({"type": "error",
                                 "error": {"type": "upstream_error", "message": str(e)}},
                                status_code=502)
        oai = chat_response_obj(model, text, usage)
        return JSONResponse(openai_to_anthropic(oai, model, input_tokens))

    if await serve_available():
        if stream:
            try:
                async def gen():
                    msg_id = oc_id("msg")
                    yield f'event: message_start\ndata: {json.dumps({"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model}})}\n\n'
                    yield f'event: content_block_start\ndata: {json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})}\n\n'
                    try:
                        async for ev in serve_chat_stream(user, model, messages):
                            if ev["kind"] == "text":
                                yield f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ev["text"]}})}\n\n'
                            elif ev["kind"] == "reasoning":
                                yield f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": ev["text"]}})}\n\n'
                            else:
                                yield f'event: content_block_start\ndata: {json.dumps({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": ev["id"], "name": ev["name"], "input": {}}})}\n\n'
                                yield f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ev["arguments"]}})}\n\n'
                    except Exception as e:
                        yield f'event: error\ndata: {json.dumps({"type": "error", "error": {"type": "api_error", "message": str(e)}})}\n\n'
                    yield f'event: content_block_stop\ndata: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n'
                    yield f'event: message_stop\ndata: {json.dumps({"type": "message_stop"})}\n\n'
                return StreamingResponse(gen(), media_type="text/event-stream")
            except Exception as e:
                print(f"[serve] anthropic sse fallback: {e}")
        else:
            try:
                text, usage, tool_calls = await serve_chat(user, model, messages)
                oai = chat_response_obj(model, text,
                                        {"input_tokens": usage["input_tokens"],
                                         "output_tokens": usage["output_tokens"]},
                                        tool_calls or None,
                                        usage.get("reasoning") or None)
                ant = openai_to_anthropic(oai, model, input_tokens)
                return JSONResponse(ant)
            except Exception as e:
                print(f"[serve] anthropic chat fallback: {e}")

    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    if tools:
        payload["tools"] = tools
    if stream:
        # reuse chat SSE then translate chunk-by-chunk is complex;
        # do non-stream upstream + convert to Anthropic SSE
        payload["stream"] = False
        try:
            r = await zen_chat_full(model, payload, session)
            data = r.json()
        except Exception as e:
            return JSONResponse({"type": "error",
                                 "error": {"type": "upstream_error", "message": str(e)}},
                                status_code=502)
        oai = data if isinstance(data, dict) and data.get("choices") else \
            chat_response_obj(model, "", None)
        ant = openai_to_anthropic(oai, model, input_tokens)
        txt = "".join(b.get("text", "") for b in ant["content"] if b.get("type") == "text")
        msg_id = ant["id"]

        async def gen():
            yield f'event: message_start\ndata: {json.dumps({"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model}})}\n\n'
            yield f'event: content_block_start\ndata: {json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})}\n\n'
            yield f'event: content_block_delta\ndata: {json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": txt}})}\n\n'
            yield f'event: content_block_stop\ndata: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n'
            yield f'event: message_stop\ndata: {json.dumps({"type": "message_stop"})}\n\n'
        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        r = await zen_chat_full(model, payload, session)
    except UpstreamError as e:
        if e.status == 429:
            return JSONResponse({"type": "error",
                                 "error": {"type": "rate_limit_error",
                                           "message": "Rate limit exceeded (free model rate limit)"}},
                                 status_code=429)
        return JSONResponse({"type": "error",
                             "error": {"type": "upstream_error",
                                       "message": f"Upstream {e.status}: {e.detail}"}},
                            status_code=502)
    except Exception as e:
        return JSONResponse({"type": "error",
                             "error": {"type": "upstream_error", "message": str(e)}},
                            status_code=502)
    data = r.json()
    if not data.get("choices"):
        return JSONResponse({"type": "error",
                             "error": {"type": "upstream_error",
                                       "message": "Invalid upstream response"}},
                            status_code=502)
    return JSONResponse(openai_to_anthropic(data, model, input_tokens))


if __name__ == "__main__":
    import uvicorn
    load_keys()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
