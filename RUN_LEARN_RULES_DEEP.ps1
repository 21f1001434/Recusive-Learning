param(
  [string]$RunsDir = "C:\hip_runs",
  [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
  [string]$Config = ".\config.yaml",
  [switch]$InstallDependencies
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($InstallDependencies) { python -m pip install -r .\requirements.txt }
$env:AIA_USE_AUTOGEN = "true"
$env:HIP_REQUIRE_AUTOGEN_075 = "true"
python -m hip_id_agent.cli learn-rules-deep `
  --config $Config `
  --input-json $InputJson `
  --customer "HIP-RULES-DEEP-DISCOVERY" `
  --runs-dir $RunsDir `
  --require-mcp
