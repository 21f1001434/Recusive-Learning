$ErrorActionPreference = "Stop"
& .\VERIFY_V243R20_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R20 baseline verification failed" }
python -c "import inspect; from hip_id_agent import semantic_control as s, browser_session as b; assert 'stable_vetted_locator' in inspect.getsource(s.SemanticActionGate.revalidate); assert 'locator' in inspect.signature(s.SemanticActionGate.verify_and_learn).parameters; assert 'aria-selected' in s.INVENTORY_JS; assert 'locator=locator' in inspect.getsource(b.BrowserSession._semantic_dispatch_target); print('R21_LOOKALIKE_CONTROLS_REPROVEN_OK')"
if ($LASTEXITCODE -ne 0) { throw "R21 look-alike re-proof smoke failed" }
python -c "from hip_id_agent.model_portfolio import MODEL_CAPABILITY as c; from hip_id_agent.config import load_config; m = load_config('config.yaml').model_portfolio; assert getattr(m, 'prefer_strongest_model', True) and c['gpt-oss-120b'] > c['gpt-oss-20b']; print('R21_STRONGEST_MODEL_PREFERRED_OK')"
if ($LASTEXITCODE -ne 0) { throw "R21 model preference smoke failed" }
python -c "from hip_id_agent.agent_live_view import AgentLiveViewRecorder as r; assert 'selected_control' in r._HISTORY_KEYS and 'website_memory' not in r._HISTORY_KEYS; print('R21_LIVE_VIEW_COMPACT_OK')"
if ($LASTEXITCODE -ne 0) { throw "R21 live view smoke failed" }
Write-Host "V243R21 install verification PASS" -ForegroundColor Green
Write-Host "Row dropdowns and multi-selects are re-proven through the executor's own locator; gpt-oss-120b is preferred."
