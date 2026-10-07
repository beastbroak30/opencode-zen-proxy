# opencode-free-proxy setup for Windows
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== checking python =="
$PY = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PY) { Write-Host "Install python from python.org first"; exit 1 }

Write-Host "== installing dependencies =="
python -m pip install --user fastapi uvicorn httpx

Write-Host "== checking opencode =="
$OC = Get-Command opencode -ErrorAction SilentlyContinue
if ($OC) {
  try { Invoke-RestMethod http://localhost:4096/global/health -TimeoutSec 3 | Out-Null
        Write-Host "opencode serve already running" }
  catch {
    Write-Host "== starting opencode serve in background =="
    Start-Process opencode -ArgumentList 'serve','--port','4096','--hostname','127.0.0.1' `
      -WindowStyle Hidden -RedirectStandardOutput opencode-serve.log -RedirectStandardError opencode-serve-err.log
    Start-Sleep 3
  }
} else {
  Write-Host "opencode not on PATH - free-tier routing needs it (https://opencode.ai)"
}

Write-Host "== fetching + verifying free models =="
python fetch_models.py

Write-Host "== starting proxy on :6446 =="
python server.py
