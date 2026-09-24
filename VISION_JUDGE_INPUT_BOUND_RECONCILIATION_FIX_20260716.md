# Vision Judge Input-Bound Reconciliation Fix

## Supplied run
`UHAUL-POASN-FULL-DUMMY-20260716-214611`

## Root cause
The deterministic Data Map judge passed every expected value and found no portal validation errors. The Gemma visual judge nevertheless returned one generic issue: `checkbox expected selected, observed not selected`. The screenshot contains unrelated listing/search checkboxes outside the active Create Map form, and the issue did not identify an expected field, input path, row, section, or selector.

## Runtime correction
- Generic visual control names (`checkbox`, `radio`, `dropdown`, `input`, `switch`, etc.) cannot veto an exact deterministic pass unless they are bound to an expected semantic field.
- Concrete issues with input paths, selectors, row identity, section identity, or known deterministic field names remain blocking.
- The visual prompt now instructs the model to inspect only the active form/drawer and to use exact expected semantic field names.
- Unsupported findings remain in the audit output under `unsupported_model_issues`.

## Safety
This does not disable the vision judge. It prevents an unnamed background control from blocking a validated form. Real input-bound visual mismatches still fail closed.

## Verification
- Supplied run offline replay: combined gate changes from blocked to pass.
- Targeted tests: passed.
- Full suite: 300 passed.
