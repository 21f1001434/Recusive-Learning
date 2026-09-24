# Rerun Guide — Document Type Structure-First Fill-Once

Use the same `run-full-dummy-fill` command. No new CLI flag is required.

## Expected Source Document Type sequence

The progress log should now show structure learning before exact filling:

```text
capture_add_form
structure learning / doctype_structure_first_audit.json
structure_learned_clean_target_form
fill_target_branch 0/29
fill_target_branch 29/29
summarize_and_write_outputs
completed
```

It must not create:

```text
doctype_post_exploration_restore_execution.json
```

and it must not use stage:

```text
post_exploration_target_restore
```

## Evidence to check

Under `source_document_type/doctype_kb/`:

- `doctype_structure_first_audit.json`
- `doctype_target_branch_execution.json`
- `doctype_add_form_after_dummy_fill_no_save.png`
- `doctype_form_kb.json`

In the target execution:

```json
{
  "pass": true,
  "failed_attempts": [],
  "attempts": [
    {
      "field": "document_type_version",
      "executor": "verification-only",
      "filled": false,
      "exact_verified": true
    }
  ]
}
```

Under `portal_form_knowledge/`, the phase knowledge should contain:

```json
{
  "learning_order": "learn complete form structure first; rebuild clean form; create exact rows; fill input graph once; freeze for verification",
  "post_fill_exploration_allowed": false,
  "form_frozen_after_exact_fill": true
}
```

## Expected no-repeat behavior

A successful Source Document Type attempt should advance to Target Document Type. A judge/evidence disagreement after exact completion may produce:

```text
completed_phase_rejudge_without_replay.json
```

It may either pass after a read-only evidence rebuild or stop fail-closed. It must not reopen and refill Source Document Type.

## Loading and SSO

The existing single-session, SSO-resume, autonomous page-health and two-minute real-loader watchdog behavior remains unchanged.
