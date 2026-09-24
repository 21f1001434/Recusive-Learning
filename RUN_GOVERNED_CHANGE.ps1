param(
    [Parameter(Mandatory=$true)][string]$Task,
    [string]$Config = ".\config.yaml",
    [string]$RunsDir = ".\runs",
    [ValidateSet("viewer","business","support","technical","admin")][string]$OperatorRole = "viewer",
    [string]$ApprovalId = "",
    [switch]$NoAdaptive,
    [switch]$AllowPortalMutation,
    [string]$Confirmation = "",
    [switch]$ForceRepeatMutation
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { $python = "python" }

$argsList = @(
    "-m", "hip_id_agent.cli", "run-governed-change", $Task,
    "--config", $Config,
    "--runs-dir", $RunsDir,
    "--operator-role", $OperatorRole
)
if ($ApprovalId) { $argsList += @("--approval-id", $ApprovalId) }
if ($NoAdaptive) { $argsList += "--no-adaptive" } else { $argsList += "--adaptive" }
if ($AllowPortalMutation) { $argsList += "--allow-portal-mutation" }
if ($Confirmation) { $argsList += @("--confirmation", $Confirmation) }
if ($ForceRepeatMutation) { $argsList += "--force-repeat-mutation" }

& $python @argsList
exit $LASTEXITCODE
