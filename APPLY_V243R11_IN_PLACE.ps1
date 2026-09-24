param(
  [string]$TargetRoot = (Get-Location).Path,
  [switch]$SkipTests,
  [switch]$SkipWheelInstall,
  [switch]$SkipSmokeCheck
)
$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $TargetRoot ".hip_patch_backups\V243R11_$timestamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$preserve = @(".env", "config.yaml", "input.json", "runs", "data\hip_memory", ".backend_runtime", ".hip_runtime")
function Should-Preserve([string]$rel) {
  foreach ($p in $preserve) {
    if ($rel -ieq $p -or $rel.StartsWith($p + "\", [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
  }
  return $false
}
$roots = @("hip_id_agent", "backend", "frontend", "webui")
if (-not $SkipTests) { $roots += "tests" }
$files = @(
  "pyproject.toml", "START_HIP_PORTAL.ps1", "RUN_PRODUCTION_E2E.ps1", "PRODUCTION_DOCTOR.ps1", "streamlit_app.py",
  "V243R11_INTERACTIVE_TEACHING_DETERMINISTIC_FINAL_20260923.md", "V243R11_FINAL_CERTIFICATION_20260923.md",
  "VERIFY_V243R11_INSTALL.ps1", "hip_portal_id_agent-2.4.3-py3-none-any.whl"
)
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
  if (-not (Test-Path $wheel)) { throw "R11 wheel missing after patch copy: $wheel" }
  Write-Host "Installing exact V243R11 wheel..." -ForegroundColor Cyan
  & python -m pip install --force-reinstall --no-deps $wheel
  if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed: $LASTEXITCODE" }
}
if (-not $SkipSmokeCheck) {
  Push-Location $TargetRoot
  try { & .\VERIFY_V243R11_INSTALL.ps1 } finally { Pop-Location }
}
Write-Host "V243R11 applied in place." -ForegroundColor Green
Write-Host "Target: $TargetRoot"
Write-Host "Backup: $backupRoot"
Write-Host "Preserved: config.yaml, .env, input.json, runs, data\hip_memory, .backend_runtime, .hip_runtime"
