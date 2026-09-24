param([string]$ProjectRoot = (Get-Location).Path)
$ErrorActionPreference = "Stop"
Push-Location $ProjectRoot
try {
  Write-Host "Checking V243R9 installation..." -ForegroundColor Cyan
  & python -c "from hip_id_agent.persistent_operator import PersistentHIPOperator; from hip_id_agent.recursive_self_improvement import RecursiveSelfImprovementEngine; from hip_id_agent.trace_self_repair import TraceSelfRepairEngine; print('R9_IMPORTS_OK')"
  if ($LASTEXITCODE -ne 0) { throw "Python import verification failed" }
  $help = (& python -m hip_id_agent --help 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0) { throw "CLI help failed" }
  if ($help -notmatch "operate-hip") { throw "operate-hip is not registered" }
  & python -m pytest --collect-only -q
  if ($LASTEXITCODE -ne 0) { throw "pytest collection failed" }
  Write-Host "V243R9 install verification PASS." -ForegroundColor Green
}
finally { Pop-Location }
