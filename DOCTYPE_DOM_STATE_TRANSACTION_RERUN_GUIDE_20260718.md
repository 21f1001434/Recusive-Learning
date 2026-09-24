# Rerun Guide — Document Type DOM-State Transactions

Use the same `run-full-dummy-fill` command and existing flags. No new flag is required.

## Expected Source Document Type behavior

1. Structure probe runs before business values.
2. A clean Create Document Type form is opened.
3. Exact rows are created from `input.json`.
4. Each graph node is uniquely bound before action.
5. Version is read and verified only.
6. Each action reaches two stable DOM-state samples.
7. Previously committed fields remain unchanged.
8. Final one-to-one form model passes.
9. Filled-form evidence is locked.
10. Judges run without reopening or refilling the form.
11. The browser proceeds to Target Document Type.

## Files to inspect

- `source_document_type/doctype_kb/doctype_form_state_model.json`
- `source_document_type/doctype_kb/doctype_target_branch_execution.json`
- `source_document_type/doctype_kb/doctype_filled_form_evidence_lock.json`
- `source_document_type/phase_execution_attempts.json`

## Required success indicators

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

Every successful mutable attempt should contain:

```json
{
  "binding_diagnostics": {
    "resolved": true,
    "score_margin": 16
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

A non-empty `protected_state_changes` list is a real causal mutation failure. The runtime should stop the Document Type phase rather than guess, refill repeatedly or continue with corrupted state.
