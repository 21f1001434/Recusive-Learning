# Runtime Fix After 20260710-000933 — True Row Add + Strict Drawer Scope

## Evidence reviewed
Run bundle: `UHAUL-POASN-FULL-DUMMY-20260710-000933`

The run is still not fully correct. Current evidence shows:

- Data Map: pass
- Source Document Type: pass
- Target Document Type: pass
- Rule: pass_with_warnings
- Source Transport Profile: failed only because aggregate report missed existing PNG
- Target Transport Profile: failed only because aggregate report missed existing PNG
- BizFlow: pass_with_warnings, 8 failed/hidden attempts

## Root causes found

### 1. BizFlow Process Step rows were never created
The audit shows `No Process Steps Added`, but Process Step +Add candidates were empty. Therefore the agent tried to fill row-specific Process Step fields before a row existed.

Fixed by adding a geometric icon-only Add detector that:
- scopes to active form/drawer only,
- rejects dropdown/search widgets,
- finds small plus/Add controls near section anchors such as Process Steps, Conditions, Actions, Attribute,
- treats `No Process Steps Added` as a valid anchor for the Process Step table Add.

### 2. BizFlow routing was accidentally selecting background page Add buttons
The routing drawer was open, but the old scanner still saw the background BizFlow listing table Add button. This produced wrong state transitions and false fill success.

Fixed by preferring the topmost visible DDS drawer/modal as the active root before searching Add buttons or controls.

### 3. Label matching was too broad
The previous control finder attached all row labels to every control on that row, so one selector could be reused for Condition Type, Operator, Value, Attribute Name, Action Name, Type, and Target.

Fixed by replacing broad row-text matching with nearest-label geometry:
- exact `label[for]`,
- label above the control with x-overlap,
- label immediately left of the control,
- no more whole-row label concatenation.

### 4. Rule Conditions row Add was rejected by safety
The correct rule row Add button label was `Create Condition`, but safety rejected it because it contained `create`.

Fixed by allowing only safe row-level `Create Condition` / Add while still blocking final Create/Submit/Save.

### 5. Transport Profile PNG false negative persisted
The TP screenshots existed in the phase folders, but the aggregate report still showed screenshots=0.

Fixed by extending the final screenshot recovery window and also reading PNG paths from phase summary JSON files before writing the final report.

## Files changed

- `hip_id_agent/bizflow_kb.py`
- `hip_id_agent/rules_kb.py`
- `hip_id_agent/dummy_fill_e2e.py`

## Validation

```text
pytest -q
204 passed in 9.01s
```

## Expected next run

The next run should:

- create Rule condition row 2 with `+ Add` / `Create Condition`,
- create BizFlow Configure Target Process Step rows before filling them,
- scope Configure Routing to the open drawer only,
- avoid writing Condition/Action values into the wrong selectors,
- recover Source/Target TP screenshots in aggregate verification.
