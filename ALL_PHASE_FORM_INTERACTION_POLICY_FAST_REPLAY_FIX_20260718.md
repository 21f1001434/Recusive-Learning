# All-Phase Form Interaction Policy + Validated Fast Replay Fix

## Goal

Apply one shared interaction policy to every HIP phase:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

The policy combines reliable parent/child handling, explicit browser events, animation stability, exact-state verification and a learned fast replay mode.

## Critical rules implemented

### Structural parent first

When a target field is absent or hidden, the runtime searches for the hidden semantic control and its safe structural controller:

- collapsed `details/summary`
- inactive tab/tabpanel
- `aria-controls` controller
- collapsed accordion button

Only non-mutating structural controllers are clicked. Save/Create/Submit/Delete/Deploy/Publish/Update/Confirm/Remove remain forbidden.

When a child still does not appear, the already-correct semantic parent is re-committed through the real DDS/browser action path. Raw value assignment is not used for radios, switches or dropdowns.

### Explicit JavaScript event contract

Radio, switch, single-select and multi-select interactions use real click/check/press actions. The runtime records:

- click/pointer/keyboard event evidence
- input/change/focusout events
- DOM mutations and selected-state changes
- exact stable final value

### Conditional child visibility gate

After a parent action, each eligible required child must become visible and uniquely bindable before the phase advances to that child.

### Animation and geometry stability

Before input, the runtime requires:

- visible element
- stable bounding box for consecutive samples
- no running CSS/Web Animations API animation
- successful `elementFromPoint()` center hit-test
- no disabled/read-only state for mutating actions

### Stale node rebind

Angular/DDS selectors are never treated as durable identity. After a rerender, the control is reacquired using:

- phase and section
- row kind and row index
- semantic field key
- `formControlName` / `ng-reflect-name`
- DDS component type
- accessible label/role

### Validation and mutation gates

An action cannot commit while the target field/group is invalid. Every action also proves that previously committed values remain unchanged.

## Learned fast replay

The runtime chooses one of two profiles.

### Learning mode

Used when the form structure is new, ambiguous or drifted.

- 160 ms polling
- 6.5 second transaction budget
- 3 stable geometry samples
- 5 second child visibility budget
- full semantic rebind and structural analysis

### Validated fast replay mode

Used when either:

- validated replay knowledge is explicitly present, or
- the live form model is one-to-one, unambiguous, fingerprinted and every currently visible binding has a strong margin.

- 70 ms polling
- 3.5 second transaction budget
- 2 stable geometry samples
- 2.6 second child visibility budget
- known branch exploration skipped
- semantic bindings reused unless Angular/DDS drift invalidates them

All safety and exact-state gates remain active in fast mode.

## New artifacts

Run-level:

- `all_phase_form_interaction_policy.json`

Per phase execution:

- `interaction_policy`
- `execution_profile`
- `fast_replay_blueprint`
- `attempts[].interaction_preparation`
- `attempts[].transaction_proof.explicit_event_proof`
- `attempts[].transaction_proof.conditional_child_visibility`
- `attempts[].transaction_proof.post_action_interaction_state`

## Failure codes

- `HIP_PHASE_AMBIGUOUS_CONTROL_BINDING`
- `HIP_PHASE_UNINTENDED_MUTATION`
- `HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT`
- `HIP_FIELD_VALIDATION_BLOCKING`
- `HIP_EXPLICIT_EVENT_PROOF_MISSING`
- `HIP_PHASE_FORM_MODEL_NOT_ONE_TO_ONE`

## Validation

- Python compilation: passed
- Focused interaction-policy regressions: 43 passed
- Complete suite: 379/379 passed
- Authenticated Dell HIP live rerun: not performed in this environment
