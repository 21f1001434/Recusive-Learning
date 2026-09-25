$ErrorActionPreference = "Stop"
& .\VERIFY_V243R15_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R15 baseline verification failed" }
python -c "import asyncio; from hip_id_agent.phase_progress import run_with_progress_watchdog as w; rows=iter([{'signature':'A','executor_progress':f't{i}'} for i in range(500)]); m=lambda: asyncio.sleep(0, next(rows)); r=asyncio.run(w(asyncio.sleep(0.5, 'done'), phase='p', marker_provider=m, checkpoint_provider=lambda:{'pass':False}, no_progress_seconds=0.2, poll_seconds=0.05)); assert r=='done'; print('R16_WATCHDOG_COUNTS_EXECUTOR_HEARTBEAT_OK')"
if ($LASTEXITCODE -ne 0) { throw "R16 watchdog heartbeat smoke failed" }
python -c "from hip_id_agent.stateful_form_runtime import publish_executor_progress as p, _node_time_budget_seconds as b; import types; pg=types.SimpleNamespace(); p(pg, phase='x', node={'node_id':'n1','field_key':'f'}, stage='start'); assert pg._hip_executor_progress['token'].endswith('n1|start|0'); assert b({})==75.0; print('R16_EXECUTOR_HEARTBEAT_AND_FIELD_BUDGET_OK')"
if ($LASTEXITCODE -ne 0) { throw "R16 executor heartbeat smoke failed" }
python -c "from hip_id_agent.mission_trace import verification_verdict as v; assert v({'status':'pass'}) is True and v({'status':'pass_with_warnings'}) is True and v({'status':'failed'}) is False and v({}) is None; print('R16_VERIFICATION_VERDICT_OK')"
if ($LASTEXITCODE -ne 0) { throw "R16 verification verdict smoke failed" }
Write-Host "V243R16 install verification PASS" -ForegroundColor Green
Write-Host "A slow but advancing phase is no longer cancelled by the no-progress watchdog; completed phases show their verification verdict."
