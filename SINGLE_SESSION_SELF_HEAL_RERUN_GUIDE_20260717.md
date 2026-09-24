# Rerun Guide

Use the same `run-full-dummy-fill` command. No new flag is required.

After the run, inspect:

- `browser_session_final_state.json`
- `runtime_self_heal/runtime_self_heal_summary.json`
- `source_document_type/doctype_kb/doctype_target_branch_execution.json`

Expected session metrics:

```json
{
  "start_count": 1,
  "sso_prompt_count": 1,
  "unique_phases_reached": [
    "data_map",
    "source_document_type",
    "target_document_type",
    "rule",
    "source_transport_profile",
    "target_transport_profile",
    "biz_flow"
  ],
  "unique_phase_count": 7,
  "single_persistent_context": true
}
```

Every Source and Target Document Type Usage attempt should show all four values and `exact_verified: true`.
