param(
  [Parameter(Mandatory=$true)][string]$Task,
  [string]$Config = ".\config.yaml",
  [string]$InputJson = ".\input.json",
  [string]$GoldenDir = ".\golden_screenshots\UHAUL-POASN",
  [switch]$Mutation,
  [string]$OperatorRole = "",
  [string]$ApprovalId = ""
)
$ErrorActionPreference = "Stop"
$argsList = @("-m","hip_id_agent.cli","run-production-e2e",$Task,"--config",$Config,"--input-json",$InputJson,"--golden-dir",$GoldenDir)
if ($Mutation) {
  if ($env:HIP_ALLOW_PORTAL_MUTATION -ne "YES") { throw "Set HIP_ALLOW_PORTAL_MUTATION=YES before a mutation run." }
  $argsList += @("--allow-portal-mutation","--confirmation","ALLOW HIP MUTATION")
  if ($OperatorRole) { $argsList += @("--operator-role",$OperatorRole) }
  if ($ApprovalId) { $argsList += @("--approval-id",$ApprovalId) }
}
& python @argsList
