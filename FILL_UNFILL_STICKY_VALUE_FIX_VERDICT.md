# Fill / Unfill Sticky Value Fix Verdict

## Problem
The latest HIP Portal dummy-fill runs show the browser can successfully select or type values, but later actions may make the same field appear blank again. This is most likely caused by DDS/Angular rerenders after combobox option capture, row-level `+ Add`, tab navigation, or nested drawer opening.

## Implemented fix

### 1. Sticky value locks in `dds_control_driver.py`
- Added a runtime lock registry: `window.__HIP_FILLED_VALUE_LOCKS`.
- Every successful `set_text_control()` and `select_dds_combobox()` now records the selector and intended non-empty value.
- Before selecting a combobox, the driver checks whether the desired value is already present and returns without touching the field.
- If a dropdown search fails and the field had a previous value, the driver restores the previous value instead of leaving the UI blank.

### 2. Restore pass after risky operations
Added `restore_filled_values()` and wired it into:
- BizFlow generic fill.
- BizFlow Configure Source row-level Attribute fills.
- BizFlow Configure Target Process Step fills.
- BizFlow Configure Routing Conditions/Actions fills.
- Before final BizFlow screenshot/evidence capture.
- Transport Profile after each field fill.
- Before Transport Profile screenshot/evidence capture.

### 3. No unsafe final actions
The restore logic only dispatches input/change/blur events on already-filled controls. It does not click Save, Create, Submit, Deploy, Delete, Publish, Update, or Confirm.

## Validation

```text
pytest -q
199 passed, 1 warning
```

## Changed files
- `hip_id_agent/dds_control_driver.py`
- `hip_id_agent/bizflow_kb.py`
- `hip_id_agent/transport_profile_kb.py`
- `FILL_UNFILL_STICKY_VALUE_FIX_VERDICT.md`
