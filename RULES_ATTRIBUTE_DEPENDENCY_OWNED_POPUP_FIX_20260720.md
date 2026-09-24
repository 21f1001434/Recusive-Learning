# Rules Attribute Dependency and Owned DDS Popup Fix — 2026-07-20

## Live failure reviewed

Run: `UHAUL-POASN-FULL-DUMMY-20260720-172346`

The Rules phase stopped before clicking the Conditions `+` because the first row was not exact:

- Condition Type: `Attributes` — committed
- Operator: `Equals` — committed
- Value: `uhaul` — committed
- Attribute Name/Unit: expected `Receiver`, actual blank

The live DDS popup showed `No options found`.

## Root cause

The mandatory Conditions transaction ran immediately after opening Create Rule, before the parent field **Document Type Name (Version)** was committed. HIP derives Attribute Name/Unit options from the selected document type. With no parent document type selected, the empty attribute list was correct portal behavior.

A second safety issue was found in the generic DDS driver: its fallback option search was page-global. The Rules page retains a background document-type accordion with hundreds of checkboxes. Text such as `Receiver` could therefore match an unrelated background checkbox instead of an option belonging to Attribute Name/Unit.

## Fix

### Parent-before-child transaction

The Rules runtime now requires this order:

1. Commit `Document Type Name (Version)` from `input.json`.
2. Verify the parent selection stuck.
3. Commit `Execute Action(s) When`.
4. Commit row Condition Type.
5. Commit Operator and Value.
6. Select Attribute Name/Unit from the popup owned by that row's exact combobox.
7. Verify the complete row.
8. Click Conditions `+` and require exactly one new FormArray identity.
9. Fill the next row.

If the document-type prerequisite is absent or cannot be committed, the Rules phase stops before entering condition values.

### Strict owned-popup selection

Attribute Name/Unit no longer uses page-global option discovery. It follows only:

- the combobox `aria-controls` / `aria-owns` popup, or
- the nearest visible DDS popup list associated with the exact control.

Only real `[role=option]` / `dds-dropdown-option` entries inside that popup are eligible. Background checkboxes, table filters, accordions, navigation, and page text are excluded.

The selected value is accepted only after the live combobox value matches the expected attribute.

## Expected U-HAUL Rules state

| Row | Condition Type | Operator | Value | Attribute Name/Unit |
|---|---|---|---|---|
| 1 | Attributes | Equals | uhaul | Receiver |
| 2 | Attributes | Contains | DELL | Sender |

## Validation

- Python compilation: passed
- Existing Rules incremental/FormArray/click-commit tests: passed
- Parent-before-child dependency regression: passed
- Strict owned-popup option regression: passed
- No-options/no-global-checkbox-fallback regression: passed
- Complete test suite: 425/425 passed
