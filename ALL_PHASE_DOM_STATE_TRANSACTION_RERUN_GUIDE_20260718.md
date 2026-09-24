# Rerun Guide — All-Phase DOM State Transactions

Use the same command. No new CLI flag is required.

## Expected per-phase evidence

For each phase, inspect:

```text
<phase>/phase_exact_state_lock.json
<phase>/**/target_branch_execution.json
<phase>/phase_execution_attempts.json
<phase>/section_judge_gate.json
```

The phase-specific target execution should contain:

```text
schema_version = hip.stateful-form-execution.v2
initial_form_state_model
final_form_state_model
completed_node_ids
transaction_policy
attempts[].binding_diagnostics
attempts[].transaction_proof
```

## Pass expectations

```json
{
  "pass": true,
  "final_form_state_model": {
    "one_to_one_pass": true,
    "missing_required": [],
    "ambiguous_nodes": [],
    "duplicate_bindings": {}
  }
}
```

Every repaired action should have:

```json
{
  "binding_diagnostics": {
    "resolved": true,
    "score_margin": 14
  },
  "transaction_proof": {
    "stability": {
      "stable": true,
      "consecutive_samples": 2
    },
    "protected_state_changes": []
  }
}
```

## Failure interpretation

- `HIP_PHASE_AMBIGUOUS_CONTROL_BINDING`: the runtime refused to guess between controls.
- `HIP_PHASE_UNINTENDED_MUTATION`: a later action changed a committed field.
- `HIP_PHASE_FORM_MODEL_NOT_ONE_TO_ONE`: input nodes do not map uniquely to live controls.
- `blocked_without_phase_replay`: exact execution passed, but evidence/judge disagreement remained; the phase was not refilled.

## Session behavior

One Chrome context and one authenticated SSO session remain shared across all phases. A locked completed phase cannot be reopened by GPT, Gemma, reporting, or the self-heal planner.
