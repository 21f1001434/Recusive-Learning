# Rerun Guide — Judge Evidence Reconciliation

1. Stop the old Python process.
2. Replace the project with the complete package, or overlay the hotfix files.
3. Preserve `data/hip_memory/portal_brain` so previously learned portal structure remains available.
4. Run the same `run-full-dummy-fill` command. No new flag is required.

## Expected Source Document Type result

After the 29 exact actions pass, the phase should not reopen or refill.

Expected artifacts:

- `source_document_type/phase_exact_state_lock.json`
- `source_document_type/doctype_kb/doctype_filled_form_evidence_lock.json`
- `source_document_type/section_judge_gate.json`
- `source_document_type/flow_pattern_memory_promotion.json`

Expected judge state:

```json
{
  "pass": true,
  "status": "pass",
  "deterministic_judge": {
    "pass": true,
    "verification_status": "pass_with_warnings"
  },
  "text_model_judge": {
    "pass": true,
    "status": "reconciled"
  },
  "vision_model_judge": {
    "pass": true,
    "status": "reconciled"
  }
}
```

The next browser phase should be `target_document_type`.

## Fail-closed conditions remain active

The phase must still stop when:

- deterministic exact value is actually missing or wrong
- a required row count is wrong
- a field action failed
- blocking portal validation remains
- the active form surface is lost
- the screenshot or final DOM is missing
- an unsafe mutation action is detected
