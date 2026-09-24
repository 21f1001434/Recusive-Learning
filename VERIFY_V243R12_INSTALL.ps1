$ErrorActionPreference = "Stop"
python -c "from hip_id_agent.continuous_learning import ContinuousPortalLearningEngine; from hip_id_agent.model_portfolio import OnPremModelPortfolioRouter; from hip_id_agent.autowebglm_bridge import AutoWebGLMRecoveryBridge; from hip_id_agent.persistent_operator import PersistentHIPOperator; print('R12_IMPORT_SMOKE_OK')"
if ($LASTEXITCODE -ne 0) { throw "R12 import smoke failed" }
$help = (& python -m hip_id_agent --help 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { throw "hip_id_agent --help failed" }
if ($help -notmatch "operate-hip") { throw "R12 smoke failed: operate-hip command not found" }
python -c "from hip_id_agent.config import AppConfig; c=AppConfig(); assert c.continuous_learning.enabled; assert c.continuous_learning.learn_from_fill; assert c.continuous_learning.learn_from_click; assert c.continuous_learning.learn_from_navigation; assert c.continuous_learning.learn_from_search; assert c.continuous_learning.promote_only_after_exact_judge_human_pass; assert c.model_portfolio.force_multi_model_during_learning; assert c.model_portfolio.force_multi_model_for_complex_tasks; assert c.model_portfolio.min_distinct_models_during_learning >= 2; print('R12H1_LEARNING_AND_MULTI_MODEL_DEFAULTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R12 defaults smoke failed" }
Write-Host "V243R12H1 hardened install verification PASS" -ForegroundColor Green
Write-Host "Continuous portal learning + multi-model learning orchestration are available."
