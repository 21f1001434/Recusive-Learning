param(
    [string]$RunsDir = "C:\hip_runs",
    [string]$Config = ".\config.yaml",
    [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
    [string]$GoldenScreenshotDir = ".\golden_screenshots\UHAUL-POASN",
    [string]$UploadAssetsDir = ".\uploads",
    [switch]$WriteHeavyEvidence,
    [string]$ResumeRunDir = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
    throw "Config file not found: $Config"
}
if (-not (Test-Path $InputJson)) {
    throw "Input JSON not found: $InputJson"
}
if (-not (Test-Path $GoldenScreenshotDir)) {
    throw "Golden screenshot directory not found: $GoldenScreenshotDir"
}

New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null

$arguments = @(
    "-m", "hip_id_agent.cli", "run-full-dummy-fill",
    "--config", $Config,
    "--customer", "UHAUL-POASN-AUTONOMOUS-MISSION",
    "--input-json", $InputJson,
    "--autonomous-mission",
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
    "--forensic-evidence",
    "--maximum-observability",
    "--golden-screenshot-dir", $GoldenScreenshotDir,
    "--upload-assets-dir", $UploadAssetsDir,
    "--runs-dir", $RunsDir
)

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

Write-Host "Starting maximum-observability dependency-aware autonomous HIP mission: Data Map through BizFlow." -ForegroundColor Cyan
Write-Host "The agent will compile a parent-child dependency path, capture structural DOM and MCP evidence, prove input-to-control coverage, commit parents before children, complete repeatable rows sequentially, exploit validated paths first, explore on drift, and auto-resume proven phases." -ForegroundColor Cyan
Write-Host "Save/Create/Submit/Delete/Deploy remain blocked. Press Ctrl+C only for an intentional external outage." -ForegroundColor Yellow

python @arguments
