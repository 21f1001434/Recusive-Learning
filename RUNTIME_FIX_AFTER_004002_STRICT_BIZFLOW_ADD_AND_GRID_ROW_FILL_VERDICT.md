# Runtime Fix After UHAUL-POASN-FULL-DUMMY-20260710-004002

## Verdict
The uploaded 004002 evidence still did not fully satisfy the golden BizFlow flow. The failure was not because the portal DOM/options were unknown; the issue was wrong row-add selection and row-field scoping.

## Evidence-based root causes

1. **Configure Source -> Attribute +Add clicked the wrong section**
   - The Source Details nested Add audit selected the `Process Steps : No Process Steps Added` button instead of the `Attributes:` button.
   - Result: the second source attribute row was never created, so row 1 fields were later reported as `row-specific control not visible`.

2. **Configure Target -> Process Step +Add clicked Reset**
   - The Process Step Add detector allowed a button labelled `Reset` because the nearby text said `No Process Steps Added`.
   - Result: no Mapping Transformer/Enricher process-step rows were created, so all process-step row fields stayed invisible.

3. **Configure Routing rows were added too early and to the wrong section**
   - Nested condition/action Add was attempted before the routing drawer was opened.
   - The Action-row add was matched to `Create Condition`, adding extra condition rows instead of an action row.

4. **Repeating rows without repeated labels broke row field matching**
   - DDS only labels the columns once. Later rows often show only `Select` controls.
   - Label-only lookup could fill the first row but failed on later rows.

5. **Transport Profile screenshot false failure still occurred**
   - The PNG existed under the TP KB folder, but aggregate verification still showed screenshots=0.

## Fixes implemented

- Strict section intent filter for BizFlow row-level Add:
  - Source Attribute rows only accept `Attributes:` candidates.
  - Target Process Step rows only accept `Process Steps` / `No Process Steps Added` candidates and reject `Reset`.
  - Routing Conditions only accept Conditions/Create Condition candidates.
  - Routing Actions only accept Actions candidates and reject Conditions/Execute Action matches.

- Configure Routing flow corrected:
  - Does not run nested row Adds before opening the route drawer.
  - Opens the routing drawer first using top-level Add.
  - Then applies Condition and Action row Adds only inside the active drawer.

- Geometry/grid-based row filling added:
  - Attributes grid rows: column order = Document Type, Attribute Name, Operator, Value.
  - Routing Conditions grid rows: column order = Condition Type, Operator, Value, Attribute Name/Unit.
  - Routing Actions grid rows: column order = Name, Type, Target.
  - This handles rows where DDS does not repeat labels on the second row.

- TP screenshot recovery strengthened:
  - The verifier now also reads top-level `screenshots` from the phase summary and recovers Windows absolute paths by filename from the local phase folder.

## Validation

```text
pytest -q
204 passed, 2 warnings in 7.46s
```

## Files changed

- `hip_id_agent/bizflow_kb.py`
- `hip_id_agent/dummy_fill_e2e.py`
- `RUNTIME_FIX_AFTER_004002_STRICT_BIZFLOW_ADD_AND_GRID_ROW_FILL_VERDICT.md`
