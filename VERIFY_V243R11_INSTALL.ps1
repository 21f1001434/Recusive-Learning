$ErrorActionPreference = "Stop"
python -c "from hip_id_agent.interactive_teaching import InteractiveTeachingStore; from hip_id_agent.deterministic_recipe import DeterministicRecipeLibrary; from hip_id_agent.phase_live_reproof import live_read_only_phase_reproof; from hip_id_agent.persistent_operator import PersistentHIPOperator; print('R11_IMPORT_SMOKE_OK')"
if ($LASTEXITCODE -ne 0) { throw "R11 import smoke failed" }
$help = (& python -m hip_id_agent --help 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { throw "hip_id_agent --help failed" }
if ($help -notmatch "operate-hip") { throw "R11 smoke failed: operate-hip command not found" }
Write-Host "V243R11 install verification PASS" -ForegroundColor Green
Write-Host "Interactive teaching + supervised deterministic promotion are available."
