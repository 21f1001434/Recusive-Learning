# Rerun Guide

## 1. Replace the current project

Extract the complete ZIP into a new folder, copy only your local `.env`/credentials into that folder, activate the existing virtual environment and install the project if required.

Do not copy an older `hip_id_agent` folder over the corrected package.

## 2. Preserve Portal Brain

Keep your existing:

`data\hip_memory\portal_brain`

The code migrates and retains previous validated/candidate/negative evidence. Do not delete it unless deliberately starting a new knowledge base.

## 3. Run the same no-save command

Your existing command is supported. For faster KB repair during controlled UAT, the original intended thresholds were:

```powershell
--kb-repair-min-confirmations 1 `
--kb-supersede-min-confirmations 2 `
```

Using `10/10` is safer but requires ten independent approved confirmations before canonical KB changes are applied. Same-run live discoveries are still available in memory without waiting for canonical promotion.

## 4. Evidence to inspect

For Source Document Type, verify these files exist:

- `doctype_kb/doctype_surface_before_repeatable_rows_initial.json`
- `doctype_kb/doctype_surface_before_control_capture_initial.json`
- `doctype_kb/doctype_surface_after_exploration_initial.json`
- `doctype_kb/doctype_surface_before_final_evidence.json`
- `doctype_kb/doctype_form_kb.json`
- `doctype_kb/doctype_dummy_fill_plan.json`
- `section_judge_gate.json`

Expected behavior:

- No candidate selector contains `div.bgColor`.
- Attribute row count is exactly 5.
- Transaction Type is `856`, never `Route Document`.
- Every current DDS selector is reacquired after rerender.
- Final screenshot visibly contains `Create Document Type`, all five rows, Cancel and Submit.
- Submit is visible for verification but never clicked.

## 5. What remains unproven

The package is locally verified but not yet proven through an authenticated complete HIP run. Do not enable final Save/Create/Submit/Deploy actions.
