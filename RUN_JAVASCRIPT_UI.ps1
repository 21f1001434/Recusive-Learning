$ErrorActionPreference = "Stop"
if (-not (Get-Command bun -ErrorAction SilentlyContinue)) { throw "Bun is required. Install Bun or add it to PATH." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python is required and the HIP venv should be activated." }
Write-Host "Starting HIP FastAPI + JavaScript UI..." -ForegroundColor Cyan
bun run platform
