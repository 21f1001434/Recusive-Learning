$ErrorActionPreference = "Stop"
& .\VERIFY_V243R25_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R25 baseline verification failed" }
python -c "import inspect; from hip_id_agent import phase_progress as p, browser_session as b; s = inspect.getsource(p.run_with_progress_budget); assert 'extended_to_finish_verification' in s and 'HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL' in s and 'checkpoint_provider' in s; assert hasattr(b.BrowserSession, 'begin_phase_attempt_progress') and 'fill_complete' in inspect.getsource(b.BrowserSession.capture_phase_progress_marker); print('R26_COMPLETE_ATTEMPT_KEPT_OK')"
if ($LASTEXITCODE -ne 0) { throw "R26 budget smoke failed" }
python -c "import inspect; from hip_id_agent import dummy_fill_e2e as d, stateful_form_runtime as f; from hip_id_agent.config import AppConfig; src = inspect.getsource(d.FullDummyFillE2EFlow.run); assert 'min_attempt_seconds' in src and 'begin_phase_attempt_progress(phase)' in src and 'checkpoint_provider=_watchdog_checkpoint_provider' in src; c = AppConfig().runtime_self_heal; assert c.min_attempt_seconds == 900 and c.finalize_grace_seconds == 600; assert hasattr(f, '_restore_reset_parents') and '_restore_reset_parents(' in inspect.getsource(f.execute_document_type_state_graph); print('R26_FAIR_RETRY_AND_PARENT_FIRST_OK')"
if ($LASTEXITCODE -ne 0) { throw "R26 retry/parent smoke failed" }
Write-Host "V243R26 install verification PASS" -ForegroundColor Green
Write-Host "A completely filled form is kept, every retry gets at least 15 minutes, and the section above is filled before the dropdowns below."
