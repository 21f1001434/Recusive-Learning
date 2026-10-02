param(
  [string]$TargetRoot = (Get-Location).Path,
  [switch]$SkipTests,
  [switch]$SkipWheelInstall,
  [switch]$SkipSmokeCheck
)
$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $TargetRoot ".hip_patch_backups\V243R36_$timestamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

# User/environment/runtime state is intentionally preserved. R36 (which includes
# R13-R35; R33 WebMCP is removed) needs no config.yaml changes: every new setting has a default
# (R36 runtime_self_heal.ask_before_reopening_max_missing: 2 -- a stalled form missing at most
# two unreadable values asks the operator instead of being reopened blank;
# model_portfolio.qualification_retries: 1, agent_chat.model_intent_fallback: true and
# agent_chat.operator_confirmation_max_unread: 3.
# R36 raises the model qualification version to 3: a kept model_selection.json is
# re-validated once at the next certification or live mission (a tie now goes to
# the more capable model);
# R35 the agent_chat section: enabled / live_frame_seconds / live_frame_quality /
# pause_poll_seconds -- the live agent chat on the right of the Control Center;
# R34 runtime_self_heal.live_map_seconds / post_complete_fill_seconds and
# autonomous_form.single_pass_when_input_json_exact; a kept config.yaml may still hold
# R33's webmcp section -- it is ignored;
# R32 exploration.explore_branches_after_fill (false: a filled form is not changed again),
# autonomous_form.stop_when_input_json_exact, runtime_self_heal.refill_probe_seconds /
# refill_loop_seconds / whitelabel_browser_restarts;
# R31 adds no setting; R30 the edit_sections section: form_wait_seconds / capture_on_edit /
# block_read_only_changes / verify_by_reopening_edit;
# R29 human_in_the_loop.input_json_exact_is_authoritative / review_learning_phase_even_when_exact
# and model_portfolio.qualification_judge_questions / _min_judge_accuracy / _max_age_days /
# _revalidate_after_judge_errors; R28 runtime_self_heal.empty_options_browser_restarts; R27 portal.navigation_render_wait_seconds / navigation_render_wait_max_seconds and
# the run_history_learning section; R26 runtime_self_heal min_attempt_seconds /
# finalize_grace_seconds / max_finalize_extensions; R25
# live_runtime_certification.job_timeout_seconds; R24 model_portfolio
# qualification_* and portal_operations.block_unrelated_changes).
# A kept config.yaml keeps its own runtime_self_heal.max_phase_wall_seconds: set
# it to 3600 for the 60-minute phase budget (R23).
# data\hip_memory keeps what the agent has learned: model_portfolio\model_selection.json
# (the one-time model qualification lock), form_structure_memory,
# runtime_recovery_ladder.json, portal_skills, from R27 run_history
# (the facts of every past run read once, and the lessons derived from them)
# and, from R30, edit_sections (each phase's learned Edit section) and, from R31,
# action_sections (Clone forms and where Deploy / Migrate go from each environment).
# runs\ (and runs\mlruns, the local MLflow store) is what R27 learns from.
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
  "V243R19_CERTIFIED_SKILLS_OPERATIONS_FAST_REPLAY_20260925.md", "APPLY_V243R19_IN_PLACE.ps1", "VERIFY_V243R19_INSTALL.ps1",
  "examples\operations_example_input.json",
  "V243R20_PLUS_ROWS_EVERY_PHASE_20260925.md", "APPLY_V243R20_IN_PLACE.ps1", "VERIFY_V243R20_INSTALL.ps1",
  "V243R21_LIVE_GATE_ROW_DROPDOWNS_BEST_MODEL_20260926.md", "APPLY_V243R21_IN_PLACE.ps1", "VERIFY_V243R21_INSTALL.ps1",
  "V243R22_SELF_HEAL_WHILE_PROGRESSING_FAIR_CHAMPION_RSI_20260927.md", "APPLY_V243R22_IN_PLACE.ps1", "VERIFY_V243R22_INSTALL.ps1",
  "V243R23_OPERATIONS_FROM_REQUESTS_SAVE_AFTER_FILL_LONGER_BUDGET_20260927.md", "APPLY_V243R23_IN_PLACE.ps1", "VERIFY_V243R23_INSTALL.ps1",
  "V243R24_LIVE_MODEL_QUALIFICATION_AND_ROW_PANEL_OPERATIONS_20260927.md", "APPLY_V243R24_IN_PLACE.ps1", "VERIFY_V243R24_INSTALL.ps1",
  "V243R25_BACKEND_RESPONSIVE_CERTIFICATION_JOB_20260928.md", "APPLY_V243R25_IN_PLACE.ps1", "VERIFY_V243R25_INSTALL.ps1",
  "V243R26_COMPLETE_ATTEMPT_KEPT_PARENT_FIRST_20260928.md", "APPLY_V243R26_IN_PLACE.ps1", "VERIFY_V243R26_INSTALL.ps1",
  "V243R27_ROUTE_RENDER_WAIT_AND_LEARNING_FROM_PAST_RUNS_20260928.md", "APPLY_V243R27_IN_PLACE.ps1", "VERIFY_V243R27_INSTALL.ps1",
  "V243R28_EMPTY_DROPDOWN_RESTART_BROWSER_20260929.md", "APPLY_V243R28_IN_PLACE.ps1", "VERIFY_V243R28_INSTALL.ps1",
  "V243R29_INPUT_JSON_EXACT_COMPLETES_PHASE_CHAMPION_REVALIDATED_20260929.md", "APPLY_V243R29_IN_PLACE.ps1", "VERIFY_V243R29_INSTALL.ps1",
  "V243R30_EDIT_SECTIONS_EVERY_PHASE_20260929.md", "APPLY_V243R30_IN_PLACE.ps1", "VERIFY_V243R30_INSTALL.ps1",
  "V243R31_CLONE_DEPLOY_MIGRATE_EVERY_PHASE_20260929.md", "APPLY_V243R31_IN_PLACE.ps1", "VERIFY_V243R31_INSTALL.ps1",
  "V243R32_STOP_WHEN_COMPLETE_AND_WHITELABEL_RESTART_20260930.md", "APPLY_V243R32_IN_PLACE.ps1", "VERIFY_V243R32_INSTALL.ps1",
  "V243R34_LIVE_INPUT_MAP_REAL_STATUS_NO_WEBMCP_20261001.md", "APPLY_V243R34_IN_PLACE.ps1", "VERIFY_V243R34_INSTALL.ps1",
  "V243R35_LIVE_AGENT_CHAT_20261001.md", "APPLY_V243R35_IN_PLACE.ps1", "VERIFY_V243R35_INSTALL.ps1",
  "V243R36_FILLED_FORM_FINISHES_OPERATOR_UNDERSTOOD_20261002.md", "APPLY_V243R36_IN_PLACE.ps1", "VERIFY_V243R36_INSTALL.ps1",
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

# R34: WebMCP (R33) is removed -- delete its files from a tree that had R33 applied.
foreach ($gone in @("hip_id_agent\webmcp.py", "tests\test_v243r33_webmcp.py", "tests\webmcp_portal_support.py",
                     "APPLY_V243R33_IN_PLACE.ps1", "VERIFY_V243R33_INSTALL.ps1", "V243R33_WEBMCP_TOOLS_HELP_COMPLETE_THE_TASK_20260930.md")) {
  $path = Join-Path $TargetRoot $gone
  if (Test-Path $path) {
    $backup = Join-Path $backupRoot $gone
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
    Move-Item -Force $path $backup
  }
}

$wheel = Join-Path $TargetRoot "hip_portal_id_agent-2.4.3-py3-none-any.whl"
if (-not $SkipWheelInstall) {
  if (-not (Test-Path $wheel)) { throw "R20 wheel missing after patch copy: $wheel" }
  Write-Host "Installing exact V243R36 wheel..." -ForegroundColor Cyan
  & python -m pip install --force-reinstall --no-deps $wheel
  if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed: $LASTEXITCODE" }
  # R27: MLflow is recorded and learned from. The wheel is installed without its
  # dependencies, so install the pinned MLflow client when it is missing. Learning
  # from the run folders works without it.
  & python -c "import mlflow" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing mlflow-skinny==3.16.1 (MLflow run tracking and learning)..." -ForegroundColor Cyan
    # protobuf stays on 5.29 (autogen-core 0.7.5 needs ~=5.29.3; MLflow's databricks-sdk skips <=5.29.4).
    & python -m pip install "mlflow-skinny==3.16.1" "protobuf>=5.29.5,<5.30"
    if ($LASTEXITCODE -ne 0) { Write-Warning "mlflow-skinny could not be installed; missions still learn from the run folders." }
  }
}

if (-not $SkipSmokeCheck) {
  Push-Location $TargetRoot
  try { & .\VERIFY_V243R36_INSTALL.ps1 } finally { Pop-Location }
}
Write-Host "V243R36 (includes R13-R35; WebMCP removed) applied in place." -ForegroundColor Green
Write-Host "Next: restart the Control Center (bun run platform). A filled form now finishes; type 'everything is filled correctly' in the Live agent chat to confirm a phase."
Write-Host "Target: $TargetRoot"
Write-Host "Backup: $backupRoot"
Write-Host "Preserved: config.yaml, .env, input.json, runs, data\hip_memory, .backend_runtime, .hip_runtime"
