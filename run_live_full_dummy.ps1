param(
  [string]$Config = ".\config.yaml",
  [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
  [string]$Customer = "UHAUL-POASN-FULL-DUMMY",
  [string]$RunsDir = "C:\hip_runs",
  [string]$VisionModel = "",
  [switch]$SkipInstall,
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Assert-Command([string]$Name, [string]$Message) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw $Message
  }
}

Assert-Command "python" "Python is not available on PATH. Activate the project virtual environment first."
Assert-Command "bun" "Bun is not available on PATH. Install Bun and reopen PowerShell before the strict MCP run."
Assert-Command "bunx" "bunx is not available on PATH. Reinstall/repair Bun before the strict MCP run."

if (-not (Test-Path $Config)) { throw "Config not found: $Config" }
if (-not (Test-Path $InputJson)) { throw "Input JSON not found: $InputJson" }
if (-not (Test-Path ".\uploads")) { throw "Required uploads directory is missing: .\uploads" }

New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null
New-Item -ItemType Directory -Force -Path ".\data\hip_memory\portal_brain\agentq\action_trajectories" | Out-Null

if (-not $SkipInstall) {
  python -m pip install -r .\requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  bun install
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

python -m compileall -q .\hip_id_agent .\tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipTests) {
  python -m pytest -q
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$arguments = @(
  "-m", "hip_id_agent.cli", "run-full-dummy-fill",
  "--config", $Config,
  "--customer", $Customer,
  "--input-json", $InputJson,
  "--fast-form-only",
  "--vision-verify",
  "--save-replay-blueprint",
  "--allow-executor-fallback",
  "--runs-dir", $RunsDir
)

if ($VisionModel.Trim()) {
  $arguments += @("--vision-model", $VisionModel.Trim())
}

Write-Host "Starting V229 hybrid HIP live run: AutoWebGLM + PyAutoGUI MCP preferred + Playwright MCP fallback." -ForegroundColor Green
Write-Host "Complete Dell SSO only in the single Chrome window opened by the agent. PyAutoGUI may execute first; Playwright MCP takes over automatically when needed." -ForegroundColor Yellow
Write-Host "Persistent memory: .\data\hip_memory\portal_brain" -ForegroundColor Cyan
Write-Host "Run evidence: $RunsDir" -ForegroundColor Cyan

& python @arguments
exit $LASTEXITCODE
