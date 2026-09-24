param(
  [string]$RunsDir = "C:\hip_runs",
  [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
  [string]$Config = ".\config.yaml",
  [switch]$NoRequireMcp
)

$ErrorActionPreference = "Stop"
$env:HIP_REQUIRE_AUTOGEN_075 = "true"
$env:AIA_USE_AUTOGEN = "true"

$argsList = @(
  "-m", "hip_id_agent.cli", "learn-bizflows-deep",
  "--config", $Config,
  "--input-json", $InputJson,
  "--customer", "HIP-BIZFLOW-DEEP-DISCOVERY",
  "--runs-dir", $RunsDir
)
if ($NoRequireMcp) { $argsList += "--no-require-mcp" } else { $argsList += "--require-mcp" }

python @argsList
exit $LASTEXITCODE
