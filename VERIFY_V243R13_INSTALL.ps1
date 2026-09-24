$ErrorActionPreference = "Stop"
& .\VERIFY_V243R12_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R12 baseline verification failed" }
python -c "from hip_id_agent.security import mask_sensitive_data as m; r=m({'authoritative_execution_verified': True, 'authorization': 'Bearer x', 'session_reused': True}); assert r['authoritative_execution_verified'] is True, r; assert r['authorization']=='***MASKED***', r; assert r['session_reused'] is True, r; print('R13_PROOF_FLAGS_NOT_MASKED_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 masking smoke failed" }
python -c "from hip_id_agent.stateful_form_runtime import _stateful_value_equal as eq; n={'phase':'data_map','field_key':'map_identifier_version','action':'verify_only','expected_value':'1'}; assert eq(n,{'value':'','placeholder':'1','disabled':True}); assert not eq(n,{'value':'','placeholder':'1'}); print('R13_READONLY_VERSION_VERIFY_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 read-only verify smoke failed" }
python -c "from hip_id_agent.autonomous_form_runtime import autonomous_target_execution as t; s=t({'pass': False, 'cycles': [], 'reason': 'x'}); assert s['pass'] is False and 'autonomous_failure_summary' in s; print('R13_FAILURE_DIAGNOSTICS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R13 diagnostics smoke failed" }
Write-Host "V243R13 install verification PASS" -ForegroundColor Green
Write-Host "Data Map learning unblock (proof-flag masking, read-only version, failure diagnostics) is active."
