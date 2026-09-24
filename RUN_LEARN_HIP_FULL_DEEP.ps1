param(
    [string]$Config = ".\config.yaml",
    [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
    [string]$RunsDir = ".\runs",
    [string]$ResumeRun = "",
    [switch]$NoRequireMcp
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:HIP_REQUIRE_AUTOGEN_075 = "true"
$env:AIA_USE_AUTOGEN = "true"

$argsList = @(
    "-u", "-m", "hip_id_agent.cli", "learn-hip-full-deep",
    "--config", $Config,
    "--input-json", $InputJson,
    "--customer", "HIP-FULL-DEEP-LEARNING",
    "--runs-dir", $RunsDir,
    "--continue-on-family-failure"
)

if ($NoRequireMcp) {
    $argsList += "--no-require-mcp"
} else {
    $argsList += "--require-mcp"
}

if ($ResumeRun) {
    $argsList += @("--resume-run", $ResumeRun)
}

python @argsList
