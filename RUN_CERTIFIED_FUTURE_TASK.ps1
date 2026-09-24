param(
    [Parameter(Mandatory=$true)][string]$Task,
    [string]$Config = ".\config.yaml",
    [string]$RunsDir = ".\runs",
    [switch]$NoAdaptive,
    [switch]$AllowPortalMutation,
    [string]$Confirmation = ""
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $python)) {
    $python = "python"
}

$argsList = @(
    "-m", "hip_id_agent.cli", "run-certified-task", $Task,
    "--config", $Config,
    "--runs-dir", $RunsDir
)
if ($NoAdaptive) { $argsList += "--no-adaptive" } else { $argsList += "--adaptive" }
if ($AllowPortalMutation) { $argsList += "--allow-portal-mutation" }
if ($Confirmation) { $argsList += @("--confirmation", $Confirmation) }

& $python @argsList
exit $LASTEXITCODE
