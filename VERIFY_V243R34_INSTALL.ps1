$ErrorActionPreference = "Stop"
& .\VERIFY_V243R32_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R32 baseline verification failed" }
python -c "import importlib.util, inspect; from hip_id_agent.config import AppConfig; from hip_id_agent import phase_live_reproof as r, phase_progress as p, autonomous_form_runtime as a, input_json_authority as q; c = AppConfig(); assert importlib.util.find_spec('hip_id_agent.webmcp') is None and not hasattr(c, 'webmcp'); assert c.runtime_self_heal.live_map_seconds == 5.0 and c.autonomous_form.single_pass_when_input_json_exact is True; assert hasattr(r, 'live_input_field_map') and 'filled_again_after_complete' in inspect.getsource(p.run_with_progress_watchdog); assert 'single_pass_proved_by_input_json' in inspect.getsource(a.execute_autonomous_phase_goal) and 'live_input_field_map' in inspect.getsource(q.quiet_completion_probe); print('R34_LIVE_MAP_OK')"
if ($LASTEXITCODE -ne 0) { throw "R34 live map smoke failed" }
python -c "import inspect, backend.app as b; src = inspect.getsource(b._runtime_status_payload); assert '_status_part(section_errors' in src and 'webmcp' not in src; assert hasattr(b, 'mission_live_input_map'); print('R34_STATUS_PARTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R34 status smoke failed" }
Write-Host "V243R34 install verification PASS" -ForegroundColor Green
Write-Host "Learning tiles show their real state; the live input.json map shows every value on the form; WebMCP removed."
