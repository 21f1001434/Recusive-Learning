param(
  [string]$Customer = "UHAL",
  [string]$PartnerQuery = "UHAL",
  [string]$SystemQuery = "UHAL-POASN",
  [string]$Config = "config.yaml",
  [string]$InputJson = "",
  [switch]$SkipInstall,
  [switch]$UseMcp
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONLEGACYWINDOWSSTDIO = "0"

if (!(Test-Path $Config)) {
  Copy-Item "config.example.yaml" $Config
  Write-Host "Created config.yaml from config.example.yaml." -ForegroundColor Yellow
}

if (!$SkipInstall) {
  Write-Host "Installing Python dependencies with corporate-friendly trusted hosts..." -ForegroundColor Cyan
  python -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
}

Write-Host "This project is configured to use installed Google Chrome, not downloaded Chromium." -ForegroundColor Green
Write-Host "Do NOT run: python -m playwright install chromium" -ForegroundColor Green
Write-Host "Correct module command format: python -m hip_id_agent.cli ..." -ForegroundColor Green

if ($UseMcp) {
  Write-Host "Checking Chrome DevTools MCP availability..." -ForegroundColor Cyan
  python -m hip_id_agent.cli chrome-devtools-check --config $Config
}

if ($InputJson -ne "") {
  python -m hip_id_agent.cli extract-ids --config $Config --customer $Customer --partner-query $PartnerQuery --system-query $SystemQuery --input-json $InputJson
} else {
  python -m hip_id_agent.cli extract-ids --config $Config --customer $Customer --partner-query $PartnerQuery --system-query $SystemQuery
}
