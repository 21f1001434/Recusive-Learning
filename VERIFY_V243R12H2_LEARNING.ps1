$ErrorActionPreference = "Stop"
Write-Host "=== V243R12H2 Learning Verification ==="
python -m compileall -q hip_id_agent
if ($LASTEXITCODE -ne 0) { throw "Python compileall failed" }
python -c "from hip_id_agent.config import AppConfig; c=AppConfig(); assert c.continuous_learning.enabled; assert c.continuous_learning.learn_from_fill; assert c.continuous_learning.learn_from_click; assert c.continuous_learning.learn_from_navigation; assert c.continuous_learning.learn_from_search; assert c.continuous_learning.learn_failed_actions_as_negative_evidence; assert c.model_portfolio.force_multi_model_during_learning; assert c.model_portfolio.min_distinct_models_during_learning >= 2; print('LEARNING_DEFAULTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "Learning defaults smoke failed" }
pytest -q tests/test_v243r12_continuous_learning_multi_model.py tests/test_v239_universal_skill_induction.py tests/test_v240_replay_policy_dreaming.py tests/test_v241_recursive_model_portfolio.py tests/test_v243_r4_learning_human_trace.py tests/test_deep_portal_learning_runtime.py tests/test_full_hip_deep_learning_certification.py
if ($LASTEXITCODE -ne 0) { throw "Learning regression failed" }
Write-Host "V243R12H2_LEARNING_VERIFICATION_PASS"
