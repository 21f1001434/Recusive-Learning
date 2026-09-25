$ErrorActionPreference = "Stop"
& .\VERIFY_V243R18_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R18 baseline verification failed" }
python -c "import tempfile, pathlib; from hip_id_agent.portal_skills import lifecycle_self_check as c; r=c(pathlib.Path(tempfile.mkdtemp())); assert r['pass'], r; print('R19_SKILL_SAVED_ONLY_AFTER_REPLAY_PROOF_OK')"
if ($LASTEXITCODE -ne 0) { throw "R19 skill lifecycle smoke failed" }
python -c "from hip_id_agent.portal_skills import canonical_operation as c, resolve_operation as r; assert [c(x) for x in ('Merger','update','Copy','Deploy','')]==['merge','edit','clone','deploy','create']; assert r({'operations':[{'phase':'rule','operation':'clone'}]},'rule')=='clone'; from hip_id_agent.config import AppConfig; a=AppConfig(); assert a.portal_skills.save_learning_only_when_certified and a.portal_operations.commit_requires_certified_skill; print('R19_OPERATIONS_AND_CONFIG_OK')"
if ($LASTEXITCODE -ne 0) { throw "R19 operations smoke failed" }
python -c "import inspect; from hip_id_agent import browser_session as b; from hip_id_agent.dds_control_driver import _value_matches_variants as m; assert m('None',['None']) and not m('None',['Delete']); assert 'combobox_option' in inspect.getsource(b.BrowserSession._assert_safe_click) and 'valueChoice' in b.CLICK_LISTENER_SCRIPT; assert 'opened_surface_no_write' in inspect.getsource(b.BrowserSession.resolve_mutation_dispatch_guard); print('R19_DROPDOWN_VALUES_NOT_MUTATIONS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R19 safety guard smoke failed" }
python -m hip_id_agent.cli run-operations --help > $null
if ($LASTEXITCODE -ne 0) { throw "R19 run-operations command missing" }
Write-Host "V243R19 install verification PASS" -ForegroundColor Green
Write-Host "Learning is saved only after a deterministic replay proves it (data\hip_memory\portal_skills); certified skills replay without model calls; input.json operations: python -m hip_id_agent.cli run-operations input.json"
