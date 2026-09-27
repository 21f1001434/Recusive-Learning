$ErrorActionPreference = "Stop"
& .\VERIFY_V243R22_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R22 baseline verification failed" }
python -c "from hip_id_agent.task_operations import task_operation_specs as t; s = t('deploy the document type XML_DellAutoASN_10_U-HAUL_ANS_IB to PROD'); assert s and s[0]['phase'] == 'source_document_type' and s[0]['operation'] == 'deploy' and s[0]['target'] == 'XML_DellAutoASN_10_U-HAUL_ANS_IB' and s[0]['values'] == {'target_environment': 'PROD'}; assert t('fill document type') == []; print('R23_REQUEST_TO_OPERATION_OK')"
if ($LASTEXITCODE -ne 0) { throw "R23 request routing smoke failed" }
python -c "import inspect; from hip_id_agent import portal_operations as p, dummy_fill_e2e as d, portal_skills as k; assert hasattr(p, 'save_verified_phase') and d.FullDummyFillOptions().save_after_fill is False; assert 'save_verified_phase(' in inspect.getsource(d.FullDummyFillE2EFlow.run); assert hasattr(k.PortalSkillStore, 'record_opener') and 'learned_opener_labels' in inspect.getsource(p.PortalOperationRunner.open_surface); print('R23_SAVE_AFTER_FILL_AND_LEARNED_OPENERS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R23 save/opener smoke failed" }
python -c "from hip_id_agent.config import AppConfig; c = AppConfig().runtime_self_heal; assert c.max_phase_wall_seconds == 3600 and c.progress_extension_seconds == 900 and c.max_progress_extensions == 8; print('R23_LONGER_PHASE_BUDGET_OK')"
if ($LASTEXITCODE -ne 0) { throw "R23 phase budget smoke failed" }
Write-Host "V243R23 install verification PASS" -ForegroundColor Green
Write-Host "Object actions from the task box go to their listing and row action; Save after verified fill is available; phases get 60 minutes plus progress extensions."
