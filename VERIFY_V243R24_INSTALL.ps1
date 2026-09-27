$ErrorActionPreference = "Stop"
& .\VERIFY_V243R23_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R23 baseline verification failed" }
python -c "import inspect; from hip_id_agent import live_runtime_certification as l, model_qualification as q, model_portfolio as m; assert 'qualify_models_on_live_page(' in inspect.getsource(l.certify_live_runtime); assert hasattr(m.OnPremModelPortfolioRouter, 'qualified_model'); s = {'controls': [{'id': 0, 'role': 'textbox', 'name': '', 'label': '', 'placeholder': 'Table search', 'row': ''}, {'id': 1, 'role': 'button', 'name': '+ Add', 'row': ''}] + [{'id': 2 + i, 'role': 'button', 'name': 'Expand the row', 'row': n, 'expanded': 'false', 'expander': True} for i, n in enumerate(['A_1', 'B_2', 'C_3'])]}; qs = q.build_questions(s); assert [x['kind'] for x in qs].count('row_action_entry') == 3 and {'search', 'create'} <= {x['kind'] for x in qs}; print('R24_LIVE_MODEL_QUALIFICATION_OK')"
if ($LASTEXITCODE -ne 0) { throw "R24 model qualification smoke failed" }
python -c "from hip_id_agent import portal_operations as p; from hip_id_agent.task_operations import task_operation_specs as t; (s,) = t('migrate document type Abbvie_SRC_DocType_IN from DEV to TEST2'); assert s['panel'] == {'environment': 'DEV'} and s['values'] == {'target_environment': 'TEST2'}; assert 'expander' in p._ROW_ACTION_JS and hasattr(p.PortalOperationRunner, 'commit_menu_choice') and p.operation_result('already_in_target') == 'EXISTING' and p.operation_result('needs_input') == 'NEEDS_INPUT'; print('R24_ROW_EXPANDER_OPERATIONS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R24 row expander smoke failed" }
python -c "from hip_id_agent.config import AppConfig; c = AppConfig(); assert c.model_portfolio.qualification_enabled and c.model_portfolio.use_qualified_model and c.portal_operations.block_unrelated_changes; print('R24_DEFAULTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "R24 defaults smoke failed" }
Write-Host "V243R24 install verification PASS" -ForegroundColor Green
Write-Host "Run 'Certify Windows runtime' once to select the model by live task performance; Document Type Edit/Clone/Migrate go through the row expander."
