param(
  [string]$TargetRoot = (Get-Location).Path,
  [switch]$SkipTests,
  [switch]$SkipWheelInstall,
  [switch]$SkipSmokeCheck
)
$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $TargetRoot ".hip_patch_backups\V243R18_$timestamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

# User/environment/runtime state is intentionally preserved. R18 (which includes
# R13-R17) is a code-only fix; config.yaml needs no changes (new settings have
# defaults). data\hip_memory keeps what the agent has learned, including
# form_structure_memory and runtime_recovery_ladder.json.
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
  "pyproject.toml", "requirements.txt", "START_HIP_PORTAL.ps1", "RUN_PRODUCTION_E2E.ps1", "PRODUCTION_DOCTOR.ps1", "streamlit_app.py",
  "README.md", "CHANGELOG.md",
  "V243R12_CONTINUOUS_LEARNING_MODEL_ORCHESTRA_20260923.md", "V243R12_FINAL_CERTIFICATION_20260923.md",
  "V243R12H1_HARDENED_AUDIT_20260923.md", "V243R12H2_LEARNING_CERTIFICATION_20260923.md",
  "VERIFY_V243R12_INSTALL.ps1", "VERIFY_V243R12H2_LEARNING.ps1", "config.example.yaml", "config.mcp-required.windows.yaml",
  "V243R13_DATAMAP_LEARNING_UNBLOCK_FIX_20260924.md", "V243R13_FINAL_VERIFICATION_20260924.md", "APPLY_V243R13_IN_PLACE.ps1", "VERIFY_V243R13_INSTALL.ps1",
  "V243R14_DOCUMENT_TYPE_FULL_FORM_FIX_20260924.md", "APPLY_V243R14_IN_PLACE.ps1", "VERIFY_V243R14_INSTALL.ps1",
  "V243R15_ALL_PHASE_FULL_FORM_AND_TASK_BOX_20260924.md", "APPLY_V243R15_IN_PLACE.ps1", "VERIFY_V243R15_INSTALL.ps1",
  "V243R16_WATCHDOG_AND_VERIFICATION_FIX_20260925.md", "APPLY_V243R16_IN_PLACE.ps1", "VERIFY_V243R16_INSTALL.ps1",
  "V243R17_FORM_VARIANTS_SELF_HEAL_AND_LEARNING_20260925.md", "APPLY_V243R17_IN_PLACE.ps1", "VERIFY_V243R17_INSTALL.ps1",
  "V243R18_STUCK_LOADER_REFRESH_RESTART_SELF_HEAL_20260925.md", "APPLY_V243R18_IN_PLACE.ps1", "VERIFY_V243R18_INSTALL.ps1",
  "hip_portal_id_agent-2.4.3-py3-none-any.whl"
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
  if (-not (Test-Path $wheel)) { throw "R18 wheel missing after patch copy: $wheel" }
  Write-Host "Installing exact V243R18 wheel..." -ForegroundColor Cyan
  & python -m pip install --force-reinstall --no-deps $wheel
  if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed: $LASTEXITCODE" }
}

if (-not $SkipSmokeCheck) {
  Push-Location $TargetRoot
  try { & .\VERIFY_V243R18_INSTALL.ps1 } finally { Pop-Location }
}
Write-Host "V243R18 stuck-loader refresh/restart self-heal patch (includes R13-R17) applied in place." -ForegroundColor Green
Write-Host "Target: $TargetRoot"
Write-Host "Backup: $backupRoot"
Write-Host "Preserved: config.yaml, .env, input.json, runs, data\hip_memory, .backend_runtime, .hip_runtime"
