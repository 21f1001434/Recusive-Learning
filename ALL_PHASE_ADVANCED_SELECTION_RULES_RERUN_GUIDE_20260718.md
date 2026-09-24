# Rerun Guide — Advanced Selection Rules

Use the same full dummy-fill command. No new CLI flag is required.

During a multi-select action, inspect the matching attempt in the phase target-execution JSON. It should contain:

```json
{
  "transaction_proof": {
    "multi_select_driver_audit": {
      "schema_version": "hip.multiselect-transaction.v2",
      "pass": true,
      "duplicate_requested_values": [],
      "missing": [],
      "extra": [],
      "selected_count_match": true
    },
    "multi_select_exact_set_proof": {
      "pass": true,
      "selection_mode_multiple": true,
      "missing": [],
      "extra": [],
      "selected_count_match": true,
      "option_universe_stable": true
    }
  }
}
```

Failure codes are explicit:

- `HIP_MULTISELECT_EXACT_SET_PROOF_FAILED`
- `HIP_PHASE_AMBIGUOUS_CONTROL_BINDING`
- `HIP_PHASE_UNINTENDED_MUTATION`
- `HIP_CONDITIONAL_CHILD_NOT_VISIBLE_AFTER_PARENT`
- `HIP_FIELD_VALIDATION_BLOCKING`

The agent must not retry by blindly clearing or refilling the complete phase. It should capture evidence and use the bounded phase self-heal policy.
