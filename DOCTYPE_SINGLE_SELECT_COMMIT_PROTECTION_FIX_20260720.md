# Document Type single-select commit/protection fix — 2026-07-20

## Live failure reviewed

Run package: `UHAUL-POASN-FULL-DUMMY-20260720-205736.zip`

The run reached the authoritative Source Document Type target-branch fill and failed at Document Identifier → Operation.

The requested Operation value was visibly and deterministically committed as `All conditions are satisfied`, but the completed-field protection gate reported Data Format Type as changed from `XML` to blank. The live control snapshot also incorrectly reported every mounted Data Format option (`CSV`, `EDIFACT`, `EDIX12`, `FLAT`, `JSON`, and `XML`) as selected.

The same evidence showed that Attribute `Derived From = ELEMENT_IN_PAYLOAD` remained expanded while its conditional Expression child was checked. Some repeated Attribute controls then became blocked or stale because the previous DDS popup/focus layer was still active.

## Root causes

1. The Document Type collector used a broad class substring selector (`[class*=item-selected]`). On this DDS build that matched presentation classes across the mounted option universe, not only the committed single option.
2. Single-select state allowed more than one `selected_values` entry even though the control contract is single selection.
3. A successful DDS selection could return while the popup/focus layer remained active. The next transaction could blur or temporarily clear a previously committed field, and conditional children might not be rendered yet.
4. The mutation guard failed immediately on that transient regression instead of first applying the existing bounded sticky-value preservation mechanism and then rechecking exact state.

## Implemented corrections

### Exact single-select state

- Removed the broad selected-class substring selector.
- Only explicit selected-state evidence is accepted: `aria-selected=true`, `data-selected=true`, `aria-checked=true`, or exact DDS selected class tokens.
- A single-select can expose at most one selected value.
- When an option universe is incorrectly presented as selected, it is collapsed only when one entry exactly matches the live value or the previously validated locked value.
- With no exact commit evidence, the selected set becomes empty and the transaction fails closed.

### DDS parent settlement

- After a successful Document Type single-select, the active element is committed and blurred without clicking the page shell.
- Parent dropdown settlement occurs before conditional-child discovery.
- The runtime waits for the fresh Angular/DDS control model after settlement.

### Completed-field protection

- The mutation guard records the initial protected-state difference.
- It performs at most one bounded restore using already validated sticky locks.
- The target and all completed controls are recaptured and rebound.
- The transaction proceeds only when every protected value is exact after restoration.
- A persistent or genuine mutation still stops the phase with `HIP_DOCTYPE_UNINTENDED_MUTATION`.

## Files changed

- `hip_id_agent/stateful_form_runtime.py`
- `tests/test_doctype_single_select_commit_protection.py`
- `CHANGELOG.md`

## Verification

- New targeted regressions: 3 passed
- Complete project suite: 431/431 passed
- Clean extracted ZIP suite: 431/431 passed
- Python compilation: passed
- ZIP integrity: passed
- Embedded secret scan: passed

## Safety

No Save, Create, Submit, Delete, Deploy, Update, Enable, Disable, Remove, Publish, Confirm, or Archive operation was introduced. Restoring a protected value is bounded to a value already committed and verified earlier in the same unsaved form transaction.
