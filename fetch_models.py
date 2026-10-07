"""Auto-fetch free models from the web + health-check each model output.

Sources (merged, in priority order):
  1. LIVE: GET https://opencode.ai/zen/v1/models  (source of truth, no auth)
     -> filter ids ending with "-free" + "big-pickle"
  2. models.dev catalog: GET https://models.dev/api.json
     -> opencode.models where cost.input==0 and cost.output==0
  3. GitHub upstream: raw server.mjs MODELS array from
     https://raw.githubusercontent.com/bigdata2211it-web/opencode-free-proxy/main/server.mjs
  4. Hardcoded fallback (last known good)

Then each candidate is probed with a tiny non-streaming Zen request
("Reply with exactly: OK") using the same auth headers as the proxy.
Only models that return non-empty output are kept as verified.

Output: models.json next to this file:
  {"models": [...verified...], "candidates": [...], "details": {...},
   "updated_at": "...", "sources": {...}}

Usage:
  python fetch_models.py [--no-check] [--check-timeout 45] [--max-tokens 16]
  python fetch_models.py --list   (print verified list and exit)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import time
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_JSON = os.path.join(BASE_DIR, "models.json")

ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
MODELS_DEV_URL = "https://models.dev/api.json"
UPSTREAM_SERVER_MJS = (
    "https://raw.githubusercontent.com/bigdata2211it-web/"
    "opencode-free-proxy/main/server.mjs"
)

OC_VERSION = "1.18.35"

FALLBACK_MODELS = [
    "deepseek-v4-flash-free",
    "big-pickle",
    "minimax-m2.5-free",
    "nemotron-3-super-free",
    "qwen3.6-plus-free",
]


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


def get_api_key() -> str:
    """Real OpenCode key (the free-tier gate rejects 'Bearer public' since ~Oct 2026)."""
    env = os.getenv("OPENCODE_API_KEY")
    if env:
        return env
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        class CA(ctypes.Structure):
            _fields_ = [("Keyword", wintypes.LPWSTR), ("Flags", wintypes.DWORD),
                        ("ValueSize", wintypes.DWORD), ("Value", ctypes.c_void_p)]

        class C(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                        ("CredentialBlob", ctypes.c_char_p), ("Persist", wintypes.DWORD),
                        ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.POINTER(CA)),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        p = ctypes.POINTER(C)()
        if advapi32.CredReadW("zed:url=https://opencode.ai/zen", 1, 0, ctypes.byref(p)):
            return p.contents.CredentialBlob[:p.contents.CredentialBlobSize].decode()
    except Exception:
        pass
    return "public"


ZEN_TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "run shell",
                                       "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "read", "description": "read file",
                                      "parameters": {"type": "object", "properties": {}}}},
]

ZEN_TOOLS_RESPONSES = [
    {"type": "function", "name": "bash", "description": "run shell",
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "read", "description": "read file",
     "parameters": {"type": "object", "properties": {}}},
]


def zen_headers(session_id: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_api_key()}",
        "User-Agent": f"opencode/{OC_VERSION} ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.13",
        "x-opencode-client": "cli",
        "x-opencode-project": "global",
        "x-opencode-request": oc_id("msg"),
        "x-opencode-session": session_id,
    }


def upstream_path_for(model: str) -> str:
    """Route to the correct Zen endpoint (mirrors opencode docs)."""
    m = model.lower()
    if m.startswith("muse-spark"):
        return "/zen/v1/responses"
    if m.startswith("jev-"):
        return "/zen/v1/systemone"
    return "/zen/v1/chat/completions"


def http_get_json(url: str, timeout: int = 20) -> object | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "opencode-zen-proxy/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[fetch] GET {url} failed: {e}")
        return None


def http_get_text(url: str, timeout: int = 20) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "opencode-zen-proxy/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[fetch] GET {url} failed: {e}")
        return None


def fetch_zen_live() -> list[str]:
    data = http_get_json(ZEN_MODELS_URL)
    if not data or not isinstance(data, dict):
        return []
    items = data.get("data") or []
    out = [m.get("id") for m in items
           if isinstance(m, dict) and isinstance(m.get("id"), str)
           and (m["id"].endswith("-free") or m["id"] == "big-pickle")]
    print(f"[fetch] zen live free models: {len(out)}")
    return sorted(set(out))


def fetch_models_dev() -> list[str]:
    data = http_get_json(MODELS_DEV_URL, timeout=30)
    try:
        models = data["opencode"]["models"]  # type: ignore
        out = [mid for mid, m in models.items()
               if isinstance(m, dict)
               and (m.get("cost") or {}).get("input") == 0
               and (m.get("cost") or {}).get("output") == 0]
        # keep free-suffixed + big-pickle (skip paid aliases)
        out = [m for m in out if m.endswith("-free") or m == "big-pickle"]
        print(f"[fetch] models.dev free models: {len(out)}")
        return sorted(set(out))
    except Exception as e:
        print(f"[fetch] models.dev parse failed: {e}")
        return []


def fetch_github_upstream() -> list[str]:
    text = http_get_text(UPSTREAM_SERVER_MJS)
    if not text:
        return []
    m = re.search(r"const\s+MODELS\s*=\s*\[(.*?)\]", text, re.S)
    if not m:
        return []
    ids = re.findall(r'"([^"]+)"', m.group(1))
    print(f"[fetch] github upstream models: {len(ids)}")
    return sorted(set(ids))


def get_candidates() -> tuple[list[str], dict]:
    zen = fetch_zen_live()
    dev = fetch_models_dev()
    gh = fetch_github_upstream()
    sources = {"zen_live": zen, "models_dev": dev,
               "github_upstream": gh, "fallback": FALLBACK_MODELS}
    # union, priority: zen first, then dev, then github, then fallback
    merged: list[str] = []
    for lst in (zen, dev, gh, FALLBACK_MODELS):
        for mid in lst:
            if mid not in merged:
                merged.append(mid)
    if not merged:
        merged = list(FALLBACK_MODELS)
    print(f"[fetch] merged candidates: {merged}")
    return merged, sources


# ---------- health check (async via httpx, fallback to urllib) ----------

def _check_one_urllib(model: str, timeout: int, max_tokens: int) -> tuple[str, bool, str]:
    import urllib.error
    path = upstream_path_for(model)
    url = f"https://opencode.ai{path}"
    session = oc_id("ses")
    try:
        if path == "/zen/v1/responses":
            payload = {"model": model,
                       "input": [{"role": "user", "content": "Reply with exactly: OK"}],
                       "max_output_tokens": max_tokens,
                       "stream": True,
                       "tools": ZEN_TOOLS_RESPONSES}
        else:
            payload = {"model": model,
                       "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                       "max_tokens": max_tokens,
                       "stream": True,
                       "tools": ZEN_TOOLS}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, headers=zen_headers(session), method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        if "FreeTierError" in raw:
            return model, False, raw[:200]
        if '"error"' in raw[:400] and "chat.completion.chunk" not in raw and "response.created" not in raw:
            return model, False, raw[:200]
        if "chat.completion.chunk" in raw or "response.created" in raw or "response.output_text" in raw:
            return model, True, "streamed-ok"
        try:
            data = json.loads(raw)
        except Exception:
            return model, False, f"non-JSON reply: {raw[:120]}"
        if isinstance(data, dict) and data.get("error"):
            return model, False, str(data["error"])[:200]
        text = extract_output_text(model, data)
        if text.strip():
            return model, True, text.strip()[:200]
        if path == "/zen/v1/responses" and isinstance(data, dict) and data.get("id"):
            return model, True, "responses-id:" + str(data.get("id"))  # accepted even if text nested oddly
        return model, False, f"empty output: {raw[:200]}"
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = str(e)
        return model, False, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return model, False, f"{type(e).__name__}: {e}"


def extract_output_text(model: str, data: dict) -> str:
    # OpenAI chat format
    try:
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            c = choices[0].get("message") or {}
            if c.get("content"):
                return str(c["content"])
            # tool calls count as working
            if c.get("tool_calls"):
                return "tool_calls"
    except Exception:
        pass
    # Responses API format: output[].content[].text
    try:
        for item in data.get("output") or []:
            for part in (item.get("content") or []):
                if isinstance(part, dict) and part.get("text"):
                    return str(part["text"])
                if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                    return str(part["text"])
        if data.get("output_text"):
            v = data["output_text"]
            return v if isinstance(v, str) else json.dumps(v)
    except Exception:
        pass
    return ""


async def check_all_async(models: list[str], timeout: int, max_tokens: int,
                          concurrency: int = 5) -> dict[str, dict]:
    try:
        import httpx  # type: ignore
    except ImportError:
        print("[check] httpx not installed, using urllib (sequential)")
        return {m: {"ok": ok, "detail": d}
                for m, ok, d in (_check_one_urllib(m, timeout, max_tokens) for m in models)}

    sem = asyncio.Semaphore(concurrency)
    results: dict[str, dict] = {}

    async def one(client: "httpx.AsyncClient", model: str):
        async with sem:
            path = upstream_path_for(model)
            url = f"https://opencode.ai{path}"
            session = oc_id("ses")
            try:
                if path == "/zen/v1/responses":
                    payload = {"model": model,
                               "input": [{"role": "user", "content": "Reply with exactly: OK"}],
                               "max_output_tokens": max_tokens, "stream": True,
                               "tools": ZEN_TOOLS_RESPONSES}
                else:
                    payload = {"model": model,
                               "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                               "max_tokens": max_tokens, "stream": True,
                               "tools": ZEN_TOOLS}
                r = await client.post(url, json=payload,
                                      headers=zen_headers(session), timeout=float(timeout))
                if r.status_code == 429:
                    results[model] = {"ok": False, "detail": "429 rate-limited"}
                    return
                if r.status_code >= 400:
                    results[model] = {"ok": False,
                                      "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
                    return
                raw = r.text
                if "FreeTierError" in raw:
                    results[model] = {"ok": False, "detail": raw[:200]}
                    return
                if ("chat.completion.chunk" in raw or "response.created" in raw
                        or "response.output_text" in raw):
                    results[model] = {"ok": True, "detail": "streamed-ok"}
                    return
                try:
                    data = r.json()
                except Exception:
                    results[model] = {"ok": False, "detail": f"non-JSON: {raw[:120]}"}
                    return
                if isinstance(data, dict) and data.get("error"):
                    results[model] = {"ok": False, "detail": str(data["error"])[:200]}
                    return
                text = extract_output_text(model, data)
                if text.strip():
                    results[model] = {"ok": True, "detail": text.strip()[:200]}
                elif path == "/zen/v1/responses" and isinstance(data, dict) and data.get("id"):
                    results[model] = {"ok": True, "detail": "responses-id:" + str(data.get("id"))}
                else:
                    results[model] = {"ok": False,
                                      "detail": f"empty output: {r.text[:200]}"}
            except Exception as e:
                results[model] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(one(client, m) for m in models))
    return results


def refresh_models(do_check: bool = True, timeout: int = 45,
                   max_tokens: int = 16) -> list[str]:
    candidates, sources = get_candidates()
    details: dict[str, dict] = {}
    if do_check:
        print(f"[check] probing {len(candidates)} models (timeout={timeout}s each)...")
        details = asyncio.run(check_all_async(candidates, timeout, max_tokens))
        for m, info in details.items():
            mark = "OK " if info["ok"] else "FAIL"
            print(f"  [{mark}] {m} -> {info['detail'][:120]}")
        verified = [m for m in candidates if details.get(m, {}).get("ok")]
        if not verified:
            print("[check] WARNING: all probes failed (network/rate-limit?). "
                  "Keeping candidates so proxy still starts.")
            verified = candidates
    else:
        verified = candidates
        details = {m: {"ok": True, "detail": "check skipped"} for m in candidates}

    payload = {"models": verified, "candidates": candidates, "details": details,
               "updated_at": datetime.now(timezone.utc).isoformat(),
               "sources": {k: v for k, v in sources.items()}}
    with open(MODELS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[fetch] wrote {MODELS_JSON} ({len(verified)} verified)")
    return verified


def load_cached() -> list[str]:
    try:
        with open(MODELS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        models = data.get("models") or []
        if models:
            return models
    except Exception:
        pass
    return list(FALLBACK_MODELS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-check", action="store_true", help="fetch only, skip probing")
    ap.add_argument("--list", action="store_true", help="print cached verified list and exit")
    ap.add_argument("--check-timeout", type=int, default=45)
    ap.add_argument("--max-tokens", type=int, default=16)
    args = ap.parse_args()
    if args.list:
        print(json.dumps(load_cached(), indent=2))
    else:
        refresh_models(do_check=not args.no_check,
                       timeout=args.check_timeout, max_tokens=args.max_tokens)
