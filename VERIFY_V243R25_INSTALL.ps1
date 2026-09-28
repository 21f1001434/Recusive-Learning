$ErrorActionPreference = "Stop"
& .\VERIFY_V243R24_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R24 baseline verification failed" }
python -c "import inspect, backend.app as a; assert '/api/mission/live-runtime-certification/status' in {getattr(r, 'path', '') for r in a.app.routes}; assert a.certify_live_runtime.__module__ == 'backend.app' and 'certify-live-runtime' in inspect.getsource(a._certification_command) and '--result-json' in inspect.getsource(a._certification_command); assert 'await run_in_threadpool(test_text_model' in inspect.getsource(a._run_live_readiness); print('R25_CERTIFICATION_IN_OWN_PROCESS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R25 certification job smoke failed" }
python -c "import inspect, backend.app as a; assert '_tail_text' in inspect.getsource(a.discovery_status) and 'GetExitCodeProcess' in inspect.getsource(a._is_running) and 'degraded' in inspect.getsource(a.runtime_status); print('R25_STATUS_POLLS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R25 status smoke failed" }
python -c "from pathlib import Path; s = Path('webui/server.js').read_text(encoding='utf-8'); j = Path('webui/app.js').read_text(encoding='utf-8'); p = Path('webui/platform.js').read_text(encoding='utf-8'); assert 'timeout: false' in s and 'idleTimeout: 255' in s and 'followCertificationJob' in j and 'blocker_count:1' not in j and 'superviseBackend()' in p; print('R25_PROXY_AND_UI_OK')"
if ($LASTEXITCODE -ne 0) { throw "R25 proxy/UI smoke failed" }
Write-Host "V243R25 install verification PASS" -ForegroundColor Green
Write-Host "Restart the Control Center (bun run platform); Certify Windows runtime now runs in its own process and shows each check as it finishes."
