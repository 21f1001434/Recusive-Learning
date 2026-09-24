param(
  [string]$Customer = "UHAL",
  [string]$PartnerQuery = "UHAL",
  [string]$SystemQuery = "UHAL-POASN",
  [string]$InputJson = "",
  [string]$Config = "config.yaml"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONLEGACYWINDOWSSTDIO = "0"
if (!(Test-Path $Config)) { Copy-Item "config.example.yaml" $Config }

Write-Host "Running HIP Partner/System ID extraction with installed Google Chrome." -ForegroundColor Green
Write-Host "No Chromium download is required." -ForegroundColor Green

if ($InputJson -ne "") {
  python -m hip_id_agent.cli extract-ids --config $Config --customer $Customer --partner-query $PartnerQuery --system-query $SystemQuery --input-json $InputJson
} else {
  python -m hip_id_agent.cli extract-ids --config $Config --customer $Customer --partner-query $PartnerQuery --system-query $SystemQuery
}
