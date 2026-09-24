param(
    [string]$RunsDir = "C:\hip_runs",
    [string]$Config = ".\config.yaml",
    [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
    [string]$GoldenScreenshotDir = ".\golden_screenshots\UHAUL-POASN",
    [string]$UploadAssetsDir = ".\uploads",
    [ValidateSet("capture", "dry_run", "validate", "write")]
    [string]$ApiMode = "capture",
    [switch]$AllowApiMutation,
    [string]$ResumeRunDir = ""
)

$ErrorActionPreference = "Stop"
python -c "from hip_id_agent.autogen_runtime import assert_autogen_075; s=assert_autogen_075(verify_imports=True); print('Microsoft AutoGen AgentChat/Core/Ext', s['required_version'], 'ready')"
if ($LASTEXITCODE -ne 0) { throw "AutoGen 0.7.5 is required. Run: python -m pip install -r .\requirements.txt" }

foreach ($required in @($Config, $InputJson, $GoldenScreenshotDir, $UploadAssetsDir)) {
    if (-not (Test-Path $required)) { throw "Required path not found: $required" }
}
New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null

if ($ApiMode -eq "write") {
    if (-not $AllowApiMutation) { throw "ApiMode=write requires -AllowApiMutation." }
    if ($env:HIP_ALLOW_API_MUTATION -ne "YES") { throw '$env:HIP_ALLOW_API_MUTATION="YES" is also required.' }
}

$argsList = @(
    "-m", "hip_id_agent.cli", "run-full-dummy-fill",
    "--config", $Config,
    "--customer", "UHAUL-POASN-ASSURED-AGENTQ-FULL-E2E",
    "--input-json", $InputJson,
    "--autonomous-mission",
    "--api-mode", $ApiMode,
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
    "--golden-screenshot-dir", $GoldenScreenshotDir,
    "--upload-assets-dir", $UploadAssetsDir,
    "--runs-dir", $RunsDir,
    "--write-heavy-evidence"
)
if ($AllowApiMutation) { $argsList += "--allow-api-mutation" }
if ($ResumeRunDir) {
    if (-not (Test-Path $ResumeRunDir)) { throw "Resume run directory not found: $ResumeRunDir" }
    $argsList += @("--resume-run", $ResumeRunDir)
}

Write-Host "Starting assured AgentQ HIP mission: Data Map -> Source/Target Document Type -> Rule -> Source/Target TP -> BizFlow" -ForegroundColor Cyan
Write-Host "Autonomous mode forces AgentQ fusion, all 3 MCPs, UI/API capture, submit interception, maximum observability, heavy evidence and mission assurance." -ForegroundColor Cyan
if ($ApiMode -ne "write") {
    Write-Host "API mode=$ApiMode: UI mutation requests are intercepted; no Dell backend mutation is sent." -ForegroundColor Yellow
} else {
    Write-Host "API WRITE MODE ENABLED. The exact observed request can be replayed only after the two-key confirmation." -ForegroundColor Red
}

python @argsList
