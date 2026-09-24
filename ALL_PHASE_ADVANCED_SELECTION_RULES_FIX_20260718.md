# HIP Portal All-Phase Advanced Selection Rules Fix — 2026-07-18

## Objective

Extend the shared form interaction policy beyond basic dropdown/radio handling so every HIP phase can safely learn and replay complex DDS/Angular widgets, especially multiple-selection controls.

## Coverage

The rules apply to Data Map, Source Document Type, Target Document Type, Rule, Source Transport Profile, Target Transport Profile, and BizFlow.

## Multi-select transaction contract

A multi-select action is successful only when all of the following are proven:

1. The target control is a real `selection=multiple` widget.
2. Requested values are normalized and duplicate-free.
3. The option universe is stable and no visible loading/filtering operation remains.
4. Every missing value is added through one exact option click.
5. Previously selected requested values remain selected after every additive click.
6. Unexpected values are removed only by clicking the exact selected option.
7. Empty-input Backspace/Delete, bulk clear, blind Enter and default Select All are forbidden.
8. The popup is reopened and `aria-checked` / `data-selected` / chip evidence proves the final exact set.
9. Selected count, exact set and multiple-selection mode agree.
10. Previously committed fields remain unchanged.

The shared runtime writes `multi_select_driver_audit` and `multi_select_exact_set_proof` inside each field transaction.

## Additional widget rules

- Searchable dropdown text is treated as a query, not a committed value.
- Typeahead values require one unique exact option click and collapsed-state verification.
- Virtualized/lazy option lists must stabilize before a missing-option decision.
- Radio groups must prove exactly one selected option in the correct semantic group.
- Checkboxes and switches use Playwright click/check primitives; direct `checked` assignment is forbidden.
- Parent changes trigger child reset and option-universe revalidation.
- Tabs and accordions require `aria-selected` / `aria-expanded` and owned-panel visibility proof.
- Repeatable rows are rebound through semantic identity after reorder/rerender.
- Uploads require `set_input_files`, filename/size evidence and completion of visible upload indicators.
- Read-only generated fields remain verification-only throughout later actions.

## Fast replay

Validated fast replay remains available. It skips broad exploration but never skips exact set, event, stability, mutation, validation or evidence checks. Any drift or ambiguity falls back to learning mode.

## Safety

The agent still never clicks Save, Create, Submit, Delete, Deploy, Publish, Update, Confirm or Remove.

## Validation

- Python compilation: passed
- Focused advanced selection/widget tests: 15 passed
- Broader shared-runtime regressions: 102 passed
- Complete suite: 388 passed
- Authenticated Dell HIP live rerun: not performed in this environment
