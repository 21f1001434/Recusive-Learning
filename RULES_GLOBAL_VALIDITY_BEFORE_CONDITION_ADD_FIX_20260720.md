# Rules Global Validity Before Conditions + Fix

## Live evidence reviewed

Run: `UHAUL-POASN-FULL-DUMMY-20260720-214635`

The first Rule condition was fully committed:

- Condition Type: `Attributes`
- Operator: `Equals`
- Value: `uhaul`
- Attribute Name/Unit: `Receiver`

The document click recorder captured two interactions on the nested `span.dds__icon--add-cir` inside **Create Condition**, but the Angular FormArray remained at one row. The live Create Rule form was still `ng-invalid`, and the screenshot showed the required Rule Name/default Action row had not yet been completed.

## Root cause

The Rules runtime attempted the Conditions `+` transaction before completing the surrounding required form state. HIP exposes the plus control, but its row-add handler performs no mutation while the foreground Create Rule form is invalid.

The old element-local click probe also missed the activation because DDS delivered the target on the nested icon and could stop bubbling or rerender the internal button.

## Implementation

Before any Conditions `+` click, the runtime now commits exact values from `input.json` for:

1. Rule Name
2. Document Type Name (Version)
3. Description, when provided
4. Default Action Name
5. Default Action Type
6. Mapping Identifier Name (Version), with one bounded child-control rebind after Action Type
7. Execute Action(s) When
8. The currently visible Condition row

The runtime then inspects the foreground Create Rule form. When the form is positively identified as `ng-invalid`, it records the invalid controls and refuses an ineffective plus click.

The click probe now listens in the capture phase on the owning fieldset and accepts only the exact Create Condition button or its descendants. This records nested icon activation even when bubbling is stopped.

## Safety

- No Save, Submit, Create, Delete, Deploy, Enable or Disable action is allowed.
- No extra Rule or Action rows are created by this prerequisite transaction.
- Values come only from the current `input.json`.
- Missing required prerequisites stop the phase before Conditions `+`.
- A plus click is successful only after an exact distinct FormArray transition `N -> N+1`.
- Existing one-to-one row identity and final live-value proofs remain mandatory.

## Validation

- Python compilation: passed
- Targeted Rules Conditions suite: 16 passed
- Complete project suite: 434/434 passed
- Action-before-Conditions regression: passed
- Known-invalid-form refusal regression: passed
- Nested icon capture-phase probe regression: passed
- Existing swallowed-click, owned-popup, row-rebind and FormArray regressions: passed
