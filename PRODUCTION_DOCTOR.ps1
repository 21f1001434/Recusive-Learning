param(
  [Parameter(Mandatory=$true)][string]$Task,
  [string]$Config = ".\config.yaml",
  [string]$InputJson = ".\input.json",
  [string]$GoldenDir = ".\golden_screenshots\UHAUL-POASN"
)
$ErrorActionPreference = "Stop"
python -m hip_id_agent.cli production-doctor $Task --config $Config --input-json $InputJson --golden-dir $GoldenDir
