# Rules Async Mapping Identifier and Existing-Name Structural Fix

## Live run reviewed

- Run: `UHAUL-POASN-FULL-DUMMY-20260720-225522`
- Phase: Rules
- Previous baseline: 434 tests
- Failure: mandatory Conditions transaction returned before filling any condition row.

## Root cause

The prerequisite transaction filled the Rule document type, Action Name and Action Type, but did not commit **Mapping Identifier Name (Version)**. The target option `DELLCoXMLASNXX08C_U-HAUL(1.0)` was present in the DDS list, but it was item 241 of 306. The previous generic dropdown driver searched only currently visible options and waited too briefly while the list was still loading.

The same live surface showed `Rule Name already exists`. Even after the mapping value is fixed, Angular keeps the Create Rule form invalid for an existing name and silently ignores the Conditions `+` handler.

## Implementation

### Owned asynchronous Mapping Identifier selection

The Rules runtime now:

1. Selects Action Type first.
2. Rebinds **Mapping Identifier Name (Version)** after the conditional field appears.
3. Follows only the listbox owned through `aria-controls` / `aria-owns`.
4. Types the exact expected mapping identifier into that DDS control.
5. Waits for loading to settle for a bounded period.
6. Finds the exact option even when it is outside the visible viewport.
7. Scrolls that exact option inside the owned listbox and clicks it.
8. Rebinds the Angular input and verifies the committed value.
9. Never falls back to background Rule-table checkboxes or page-global text matches.

### Existing Rule Name structural transaction

When the exact Rule Name produces `Rule Name already exists`:

1. The exact input name is recorded first.
2. A unique bounded `__PROBE_<token>` name is filled only to enable unsaved structural `+` operations.
3. No Save, Create, Submit, Update, Delete or Deploy action is allowed.
4. Required Actions and the first Condition row are completed.
5. The Conditions `+` must produce exactly one distinct FormArray row.
6. After all required rows exist, the exact input Rule Name is restored.
7. The restore is verified from the fresh live input.
8. The expected existing-object validation is accepted only when the read-only Rule inventory resolves that exact name.

## Diagnostics improved

A future mandatory Conditions failure now includes:

- prerequisite attempts,
- missing/failed required fields,
- transient-name state,
- mapping DDS popup evidence,
- click evidence,
- exact live row proof.

## Verification

- Python compilation: passed
- Targeted Rules tests: 20 passed
- Complete source-tree suite: 438 passed
- Clean extracted package suite: 438 passed
- ZIP integrity: passed
- Embedded secret scan: passed
