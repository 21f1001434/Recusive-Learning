# Rules Conditions Click-Commit Fix — 2026-07-20

## Live failure analyzed

Run: `UHAUL-POASN-FULL-DUMMY-20260720-123425`

The Rules form still contained only one physical Angular condition row. The first condition had been overwritten with the second input condition, and the final state model correctly stopped with `HIP_PHASE_FORM_MODEL_NOT_ONE_TO_ONE`.

## Root cause

The `Attribute Name/Unit` DDS combobox was still expanded when the agent attempted to click the `Create Condition` plus button.

The browser evidence showed:

1. `pointerdown` reached the Conditions plus button.
2. That pointerdown caused the active Attribute dropdown to emit `change` and `focusout`.
3. No `click` event reached the plus button.
4. No new Angular FormArray row was created.
5. The earlier runtime caught the mandatory row-creation failure as a warning and continued.
6. The later exact-fill/state-graph pass reused row 1 for input row 2 and overwrote `Receiver / Equals / uhaul` with `Sender / Contains / DELL`.

## Fix implemented

### DDS settlement before Create Condition

Before clicking the Conditions plus button, the runtime now:

- presses Escape when available;
- dispatches input/change/focusout/blur on the active control;
- invokes the shared non-clicking DDS popup closer;
- records expanded combobox and visible popup state.

It never clicks the page shell or overlay to dismiss a dropdown.

### Exact click proof

A one-shot listener is attached to the exact Conditions plus button before each click. The audit records whether the button received a real `click` event, not merely `pointerdown`.

### Bounded safe retry

At most two physical clicks are allowed. A second click is allowed only when:

- the first click caused no row-count change;
- every existing condition row still exactly matches the input prefix;
- the Create Condition button is rebound inside the visible Create Rule drawer.

Any non-zero wrong row transition stops immediately.

### Mandatory fail-closed transaction

Failure to create and verify all input condition rows now raises immediately. It is no longer converted into a warning. Generic fill and state-graph reconciliation are forbidden from continuing after a failed row-creation transaction.

### No duplicate condition refill

After the dedicated Conditions transaction succeeds, the later exact-fill pass fills Rule Details and Actions only. It does not refill Conditions and cannot overwrite an earlier condition row.

## Expected final Rules conditions

| Row | Condition Type | Operator | Value | Attribute Name/Unit |
|---|---|---|---|---|
| 1 | Attributes | Equals | uhaul | Receiver |
| 2 | Attributes | Contains | DELL | Sender |

## Validation

- Python compilation: passed
- Full project suite: 419/419 passed
- First-click-swallowed DDS regression: passed
- Mandatory fail-closed regression: passed
- Existing FormArray one-to-one regressions: passed
- MultiOn dependency: absent

An authenticated Dell HIP rerun was not possible in the build environment because Dell SSO and private portal access are unavailable.
