# Single Session + Self-Heal Run Reanalysis — 2026-07-17

## Verdict

The persistent browser/session contract worked. Chrome started once, Dell SSO was requested once, and the same browser context was reused. The run did not complete seven phases: the recorded borrow count of seven represented two Data Map attempts and five Source Document Type attempts. Only two unique phases were reached.

## Data Map

Attempt 1 was blocked by a judge evidence mismatch. Runtime self-heal refreshed evidence and reran the phase. Attempt 2 passed. This confirms that the self-heal loop is connected and can resolve a real independent-judge retry.

## Source Document Type

Attempts 1 and 3 were blocked by a small Dell DDS loading marker positioned at the bottom-right of the page. It did not cover the active form controls. The integrated autonomous page-health logic treats this as passive when hit-testing proves the requested control remains interactable.

Attempts 2, 4 and 5 were not portal failures. The saved final DOM contains all four required Usage values in all five attribute rows:

- Flow Identifier Expression
- Logging
- Mapping
- Routing

Dell removes the visible `Usage` label from repeated rows after selected chips render. The collector therefore gave rows 2–5 an empty semantic key even though their DDS multiple-selection state was exact. Final reconciliation could not resolve those rows and reported `actual_value: []`. Self-heal then reopened a form that was already correct until the repeated-signature budget stopped fail-closed.

## Implemented Correction

1. A multiple-selection combobox inside an exact `Attributes To Configure` row is structurally identified as `attribute_usage`.
2. The resolver accepts that unique row-local structure when Dell drops the repeated label.
3. Ambiguous rows with more than one multi-select still fail closed.
4. Exact verification reads selected options and chips, not the empty DDS search input.
5. Session logs now distinguish attempt borrows from unique phases reached.

## Expected Next Run

The same saved Source Document Type state reconciles as exact without refilling. The run should proceed to Target Document Type after Source Document Type judges pass.

## Validation

- Python compilation: passed
- Targeted regression tests: 25 passed
- Full automated suite: 335 passed
- Authenticated live HIP rerun: not performed in this environment
