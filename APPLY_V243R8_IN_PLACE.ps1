param(
  [string]$TargetRoot = (Get-Location).Path,
  [switch]$SkipTests,
  [switch]$SkipWheelInstall,
  [switch]$SkipSmokeCheck
)
$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $TargetRoot ".hip_patch_backups\V243R8_$timestamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

$preserve = @(
  ".env", "config.yaml", "input.json", "runs", "data\hip_memory",
  ".backend_runtime", ".hip_runtime"
)

function Should-Preserve([string]$rel) {
  foreach ($p in $preserve) {
    if ($rel -ieq $p -or $rel.StartsWith($p + "\", [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
  }
  return $false
}

$roots = @("hip_id_agent", "backend", "frontend", "webui")
$files = @(
  "pyproject.toml", "START_HIP_PORTAL.ps1", "RUN_PRODUCTION_E2E.ps1",
  "PRODUCTION_DOCTOR.ps1", "streamlit_app.py",
  "V243R8_PERSISTENT_OPERATOR_RSI_IN_PLACE_20260922.md",
  "VERIFY_V243R8_INSTALL.ps1",
  "hip_portal_id_agent-2.4.3-py3-none-any.whl"
)
if (-not $SkipTests) { $roots += "tests" }

foreach ($root in $roots) {
  $srcRoot = Join-Path $PatchRoot $root
  if (-not (Test-Path $srcRoot)) { continue }
  Get-ChildItem -Path $srcRoot -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($PatchRoot.Length).TrimStart('\')
    if (Should-Preserve $rel) { return }
    $dst = Join-Path $TargetRoot $rel
    if (Test-Path $dst) {
      $backup = Join-Path $backupRoot $rel
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
      Copy-Item -Force $dst $backup
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item -Force $_.FullName $dst
  }
}
foreach ($rel in $files) {
  $src = Join-Path $PatchRoot $rel
  if (-not (Test-Path $src)) { continue }
  if (Should-Preserve $rel) { continue }
  $dst = Join-Path $TargetRoot $rel
  if (Test-Path $dst) {
    $backup = Join-Path $backupRoot $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
    Copy-Item -Force $dst $backup
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
  Copy-Item -Force $src $dst
}

$wheel = Join-Path $TargetRoot "hip_portal_id_agent-2.4.3-py3-none-any.whl"
if (-not $SkipWheelInstall) {
  if (-not (Test-Path $wheel)) { throw "R8 wheel is missing after patch copy: $wheel" }
  Write-Host "Installing exact V243R8 wheel into the active Python environment..." -ForegroundColor Cyan
  & python -m pip install --force-reinstall --no-deps $wheel
  if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed with exit code $LASTEXITCODE" }
}

if (-not $SkipSmokeCheck) {
  Push-Location $TargetRoot
  try {
    & python -c "from hip_id_agent.persistent_operator import PersistentHIPOperator; from hip_id_agent.recursive_self_improvement import RecursiveSelfImprovementEngine; print('R8_IMPORT_SMOKE_OK')"
    if ($LASTEXITCODE -ne 0) { throw "R8 import smoke failed" }
    $help = (& python -m hip_id_agent --help 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "hip_id_agent --help failed" }
    if ($help -notmatch "operate-hip") { throw "R8 smoke failed: operate-hip command was not found" }
    Write-Host "R8 smoke check PASS: operate-hip is available." -ForegroundColor Green
  }
  finally { Pop-Location }
}

Write-Host "V243R8 applied in place." -ForegroundColor Green
Write-Host "Target: $TargetRoot"
Write-Host "Backup: $backupRoot"
Write-Host "Preserved: config.yaml, .env, input.json, runs, data\hip_memory, .backend_runtime, .hip_runtime"
Write-Host "Wheel: exact bundled 2.4.3 R8 build installed into active Python unless -SkipWheelInstall was used"
Write-Host "Run: python -m hip_id_agent --help"
Write-Host "Then: hip-agent operate-hip <task> ..."
