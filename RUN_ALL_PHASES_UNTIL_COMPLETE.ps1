param(
    [string]$RunsDir = "C:\hip_runs",
    [string]$Config = ".\config.yaml",
    [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
    [string]$GoldenScreenshotDir = ".\golden_screenshots\UHAUL-POASN",
    [string]$UploadAssetsDir = ".\uploads",
    [switch]$WriteHeavyEvidence,
    [switch]$Resume,
    [string]$ResumeRunDir = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
    throw "Config file not found: $Config"
}
if (-not (Test-Path $InputJson)) {
    throw "Input JSON not found: $InputJson"
}

New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null

$arguments = @(
    "-m", "hip_id_agent.cli", "run-full-dummy-fill",
    "--config", $Config,
    "--customer", "UHAUL-POASN-ALL-PHASES-UNTIL-COMPLETE",
    "--input-json", $InputJson,
    "--all-phases-until-complete",
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
    "--require-mcp",
    "--forensic-evidence",
    "--golden-screenshot-dir", $GoldenScreenshotDir,
    "--upload-assets-dir", $UploadAssetsDir,
    "--runs-dir", $RunsDir
)

if ($ResumeRunDir -and $Resume) {
    throw "Use either -Resume (auto) or -ResumeRunDir <path>, not both"
}
if ($ResumeRunDir) {
    if (-not (Test-Path $ResumeRunDir)) { throw "Resume run directory not found: $ResumeRunDir" }
    $arguments += @("--resume-run", $ResumeRunDir)
} elseif ($Resume) {
    $arguments += "--auto-resume"
}

if ($WriteHeavyEvidence) {
    $arguments += "--write-heavy-evidence"
} else {
    $arguments += "--no-write-heavy-evidence"
}

if ($Resume -or $ResumeRunDir) {
    Write-Host "Mission resume enabled: judge-approved completed phases from the prior run are adopted fail-closed; unproven phases re-execute live." -ForegroundColor Green
}
Write-Host "Starting all seven HIP phases with progress-driven exploration/exploitation self-heal." -ForegroundColor Cyan
Write-Host "Validated paths are exploited first; exploration is used only for drift or failure." -ForegroundColor Cyan
Write-Host "Press Ctrl+C only when Dell SSO, HIP, MCP, network, or AIA is intentionally unavailable." -ForegroundColor Yellow

python @arguments
