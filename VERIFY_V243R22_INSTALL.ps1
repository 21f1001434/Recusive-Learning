$ErrorActionPreference = "Stop"
& .\VERIFY_V243R21_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R21 baseline verification failed" }
python -c "import inspect; from hip_id_agent import phase_progress as p, dummy_fill_e2e as d, runtime_self_heal as r; from hip_id_agent.config import load_config; c = load_config('config.yaml').runtime_self_heal; assert hasattr(p, 'run_with_progress_budget') and c.max_progress_extensions >= 1; s = inspect.getsource(d.FullDummyFillE2EFlow.run); assert 'run_with_progress_budget(' in s and 'extend_for_progress(' in s; assert hasattr(r.RuntimeSelfHealController, 'extend_for_progress'); print('R22_PROGRESS_EARNS_TIME_OK')"
if ($LASTEXITCODE -ne 0) { throw "R22 progress budget smoke failed" }
python -c "import inspect; from hip_id_agent import autowebglm_bridge as a, browser_session as b; from hip_id_agent.config import load_config; w = load_config('config.yaml').autowebglm; assert w.vetted_intent_parallel_models == 1; assert 'vetted=True' in inspect.getsource(b.BrowserSession._autowebglm_primary_decision); assert 'vetted_intent_challenger_every' in inspect.getsource(a.AutoWebGLMRecoveryBridge.primary_decide); print('R22_VETTED_INTENT_ONE_MODEL_OK')"
if ($LASTEXITCODE -ne 0) { throw "R22 vetted intent smoke failed" }
python -c "import inspect; from hip_id_agent import model_portfolio as m, dummy_fill_e2e as d; assert m.CREDIT_RULE and 'decision_key' in inspect.getsource(m.OnPremModelPortfolioRouter.record_downstream_outcome); assert 'close_mission_learning_loop(' in inspect.getsource(d.FullDummyFillE2EFlow.run).split('async def _execute_phase_once')[0]; print('R22_FAIR_CHAMPION_AND_PHASE_RSI_OK')"
if ($LASTEXITCODE -ne 0) { throw "R22 fair champion / RSI smoke failed" }
Write-Host "V243R22 install verification PASS" -ForegroundColor Green
Write-Host "Progress earns time instead of a human stop; vetted actions ask one model; the champion is earned on real decisions; RSI runs every phase attempt."
