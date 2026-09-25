$ErrorActionPreference = "Stop"
& .\VERIFY_V243R17_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R17 baseline verification failed" }
python -c "import asyncio; from hip_id_agent.phase_progress import run_with_progress_watchdog as w; rows=iter([{'signature':'A','blocking_loader':True,'executor_progress':f't{i}'} for i in range(500)]); m=lambda: asyncio.sleep(0, next(rows)); loop=asyncio.new_event_loop(); t=loop.create_task(w(asyncio.sleep(30), phase='p', marker_provider=m, checkpoint_provider=lambda:{'pass':False}, no_progress_seconds=0.1, poll_seconds=0.02, blocking_wait_seconds=0.3)); loop.run_until_complete(asyncio.wait([t])); r=t.exception(); assert getattr(r, 'code', '')=='HIP_PORTAL_LOADING_STUCK', r; print('R18_WATCHDOG_LOADER_STUCK_OK')"
if ($LASTEXITCODE -ne 0) { throw "R18 loader-aware watchdog smoke failed" }
python -c "from hip_id_agent.runtime_self_heal import RuntimeSelfHealController as C; assert C.classify_failure('HIP_PORTAL_LOADING_STUCK: x')=='portal_loading_stuck' and C.classify_failure('HIP_PHASE_NO_PROGRESS_WATCHDOG: x')=='phase_no_progress'; assert C.CLASS_ACTIONS['portal_loading_stuck']==('refresh_page_and_reopen','restart_browser_session'); print('R18_RECOVERY_LADDER_CLASSES_OK')"
if ($LASTEXITCODE -ne 0) { throw "R18 ladder classification smoke failed" }
python -c "import inspect; from hip_id_agent.browser_session import BrowserSession as B; src=inspect.getsource(B._launch_managed_context_with_fallback)+inspect.getsource(B.restart); assert '_relaunching_selected_browser' in src and 'fresh_tab_in_attached_browser' in src; print('R18_SAME_BROWSER_RESTART_ALLOWED_OK')"
if ($LASTEXITCODE -ne 0) { throw "R18 browser restart smoke failed" }
python -c "import json; from hip_id_agent.aia_client import extract_json_object as j; assert j('analysis {x} assistantfinal'+json.dumps({'a':2}))=={'a':2} and j(json.dumps({'a':1}))=={'a':1}; from hip_id_agent.environment_faults import is_environment_fatal as f; assert f(RuntimeError('HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH: x')) and not f(RuntimeError('element not visible')); print('R18_GPT_OSS_AND_FATAL_ERRORS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R18 gpt-oss/fatal error smoke failed" }
Write-Host "V243R18 install verification PASS" -ForegroundColor Green
Write-Host "A stuck portal loading spinner is refreshed, then the browser is closed and reopened, and the phase resumes by itself; which step works is learned in data\hip_memory\runtime_recovery_ladder.json."
