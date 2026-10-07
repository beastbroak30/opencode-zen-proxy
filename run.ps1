# One-click auto runner (PowerShell). Uses uv + global python libs.
# Usage:  powershell -ExecutionPolicy Bypass -File run.ps1
#         .\run.ps1 -NoCheck      (skip probing, fast start)
#         .\run.ps1 -FetchOnly    (only update models.json)
param([switch]$NoCheck, [switch]$FetchOnly, [switch]$SkipInstall)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "uv not found, installing via astral.sh..." -ForegroundColor Yellow
  irm https://astral.sh/uv/install.ps1 | iex
}

if (-not $SkipInstall) {
  Write-Host "== installing global libs with uv ==" -ForegroundColor Cyan
  uv pip install --system fastapi uvicorn httpx
}

$args2 = @("run.py")
if ($NoCheck)   { $args2 += "--no-check" }
if ($FetchOnly) { $args2 += "--fetch-only" }
if ($SkipInstall) { $args2 += "--skip-install" }

Write-Host "== auto fetch + verify + start ==" -ForegroundColor Cyan
python @args2
