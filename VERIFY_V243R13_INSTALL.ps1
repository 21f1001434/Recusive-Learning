$ErrorActionPreference = "Stop"
& .\VERIFY_V243R12_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R12 baseline verification failed" }
python -c "from hip_id_agent.security import mask_sensitive_data as m; r=m({'authoritative_execution_verified': True, 'authorization': 'Bearer x', 'session_reused': True}); assert r['authoritative_execution_verified'] is True, r; assert r['authorization']=='***MASKED***', r; assert r['session_reused'] is True, r; print('R13_PROOF_FLAGS_NOT_MASKED_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 masking smoke failed" }
python -c "from hip_id_agent.stateful_form_runtime import _stateful_value_equal as eq; n={'phase':'data_map','field_key':'map_identifier_version','action':'verify_only','expected_value':'1'}; assert eq(n,{'value':'','placeholder':'1','disabled':True}); assert not eq(n,{'value':'','placeholder':'1'}); print('R13_READONLY_VERSION_VERIFY_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 read-only verify smoke failed" }
python -c "from hip_id_agent.autonomous_form_runtime import autonomous_target_execution as t; s=t({'pass': False, 'cycles': [], 'reason': 'x'}); assert s['pass'] is False and 'autonomous_failure_summary' in s; print('R13_FAILURE_DIAGNOSTICS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 diagnostics smoke failed" }
python -c "from hip_id_agent.security import mask_sensitive_data as m; r=m({'session_id':'s1','task_tokens':['create','map'],'access_token':'t'}); assert r['session_id']=='s1' and r['task_tokens']==['create','map'] and r['access_token']=='***MASKED***', r; print('R13_LEARNING_MEMORY_KEYS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 learning-memory masking smoke failed" }
python -c "from hip_id_agent.stateful_form_runtime import existing_object_validation as ex, _value_equal as eq; assert ex({'field_key':'document_type_name'},{'blockingValidation':True,'validationMessage':'Name already exists'}); assert not ex({'field_key':'attribute_name'},{'blockingValidation':True,'validationMessage':'Attribute Name already exists'}); assert eq({'action':'select_radio','expected_value':'Enabled'},{'role':'switch','checked':True}); print('R13_ALL_PHASE_EXISTING_OBJECT_AND_SWITCH_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 all-phase smoke failed" }
python -c "from hip_id_agent.mission_learning import close_mission_learning_loop; from hip_id_agent.dummy_fill_e2e import close_mission_learning_loop as wired; print('R13_MISSION_RSI_WIRED_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 mission RSI wiring smoke failed" }
Write-Host "V243R13 install verification PASS" -ForegroundColor Green
Write-Host "All-phase completion + learning unblock (proof flags, learning memory, mission RSI, existing objects, read-only fields, diagnostics) is active."
