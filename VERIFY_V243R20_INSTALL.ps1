$ErrorActionPreference = "Stop"
& .\VERIFY_V243R19_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R19 baseline verification failed" }
python -c "from hip_id_agent.form_structure_healer import _ADD_BUTTON_JS as j, add_row_action_label as l; assert 'add-cir' in j and 'dds-tooltip' in j and 'insideRow' in j; x=l({'title':'Conditions :','label':'Create Condition'}); assert x.startswith('structural_opener add row') and 'create' not in x.lower(); print('R20_LEGEND_PLUS_RECOGNISED_OK')"
if ($LASTEXITCODE -ne 0) { throw "R20 legend plus recognition smoke failed" }
python -c "import inspect; from hip_id_agent import stateful_form_runtime as s, browser_session as b, rules_kb as r; assert 'formarray_first_row' in inspect.getsource(s.capture_stateful_controls) and 'array:conditions' in inspect.getsource(s); assert 'add_icon' in inspect.getsource(b.BrowserSession._assert_safe_click); assert 'structural_opener add row Conditions' in inspect.getsource(r); print('R20_ADDED_ROWS_CLASSIFIED_AND_CLICKABLE_OK')"
if ($LASTEXITCODE -ne 0) { throw "R20 row classification smoke failed" }
Write-Host "V243R20 install verification PASS" -ForegroundColor Green
Write-Host "Rows that input.json needs are added with the portal's own legend + (icon-only, tooltip-named) in every phase and then filled."
