param(
    [string]$RunsDir = "C:\hip_runs",
    [string]$Config = ".\config.yaml",
    [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
    [string]$GoldenScreenshotDir = ".\golden_screenshots\UHAUL-POASN",
    [string]$UploadAssetsDir = ".\uploads",
    [ValidateSet("capture", "dry_run", "validate", "write")]
    [string]$ApiMode = "capture",
    [switch]$AllowApiMutation,
    [switch]$WriteHeavyEvidence,
    [string]$ResumeRunDir = ""
)

$ErrorActionPreference = "Stop"

foreach ($required in @($Config, $InputJson, $GoldenScreenshotDir)) {
    if (-not (Test-Path $required)) {
        throw "Required path not found: $required"
    }
}

if ($ApiMode -eq "write") {
    if (-not $AllowApiMutation) {
        throw "ApiMode=write requires -AllowApiMutation. UI submit capture itself remains network-blocked."
    }
    if ($env:HIP_ALLOW_API_MUTATION -ne "YES") {
        throw 'ApiMode=write also requires: $env:HIP_ALLOW_API_MUTATION="YES"'
    }
}

New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null

$arguments = @(
    "-m", "hip_id_agent.cli", "run-full-dummy-fill",
    "--config", $Config,
    "--customer", "UHAUL-POASN-AGENTQ-UI-API-MISSION",
    "--input-json", $InputJson,
    "--autonomous-mission",
    "--agentq-crawler-fusion",
    "--dual-ui-api",
    "--capture-submit-api",
    "--api-mode", $ApiMode,
    "--require-api-capture",
    "--fast-form-only",
    "--portal-brain",
    "--import-unified-kb",
    "--require-unified-kb",
    "--self-heal-kb",
    "--revalidate-known-parent-branches",
    "--kb-repair-min-confirmations", "10",
    "--kb-supersede-min-confirmations", "10",
    "--exploration-agent",
    "--explore-parent-branches",
    "--exploration-max-values-per-parent", "100",
    "--section-judge",
    "--require-text-judge",
    "--require-vision-judge",
    "--section-judge-max-repairs", "5",
    "--vision-verify",
    "--vision-model", "gemma-3-27b-it",
    "--save-replay-blueprint",
    "--allow-executor-fallback",
    "--runtime-self-heal-until-complete",
    "--forensic-evidence",
    "--maximum-observability",
    "--golden-screenshot-dir", $GoldenScreenshotDir,
    "--upload-assets-dir", $UploadAssetsDir,
    "--runs-dir", $RunsDir
)

if ($AllowApiMutation) {
    $arguments += "--allow-api-mutation"
}
if ($ResumeRunDir) {
    if (-not (Test-Path $ResumeRunDir)) {
        throw "Resume run directory not found: $ResumeRunDir"
    }
    $arguments += @("--resume-run", $ResumeRunDir)
}
if ($WriteHeavyEvidence) {
    $arguments += "--write-heavy-evidence"
} else {
    $arguments += "--no-write-heavy-evidence"
}

Write-Host "Starting AgentQ UI/API autonomous HIP mission: Data Map through BizFlow." -ForegroundColor Cyan
Write-Host "UI is filled and verified sequentially; observed API contracts and payloads are extracted from the same authenticated browser session." -ForegroundColor Cyan
if ($ApiMode -eq "write") {
    Write-Host "API WRITE ENABLED: the exact in-memory captured request may be sent after UI verification." -ForegroundColor Red
} else {
    Write-Host "API mode=$ApiMode. UI Create/Save/Submit requests are captured behind a Playwright network-abort barrier and cannot reach Dell." -ForegroundColor Yellow
}
Write-Host "Use Ctrl+C only for an intentional external outage." -ForegroundColor Yellow

python @arguments
