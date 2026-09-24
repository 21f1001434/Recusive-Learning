# Rerun Guide

Use the same `run-full-dummy-fill` command. Deep portal learning is enabled by default through `config.yaml`.

The currently running Python process will not load this patch dynamically. Apply the complete package or hotfix before the next run.

## What to inspect

At startup:

- `portal_learning/mcp_learning_capability_matrix.json`

For each reached phase:

- `<phase>/portal_learning/attempt_01/portal_learning_summary.json`

A successful phase should show:

```json
{
  "trust": "validated",
  "deterministic_pass": true,
  "judge_pass": true,
  "storage_values_captured": false
}
```

After multiple runs, inspect the persistent phase file under the configured Portal Brain directory. It now contains:

- `page_fingerprints`
- `api_contracts`
- `validation_rules`
- `state_transitions`
- `console_signatures`
- `coverage_history`
- `drift_history`
- `autonomous_learning_runs`

## Performance tracing

Performance tracing is enabled only on retry attempts by default. Set:

```yaml
portal_learning:
  performance_trace_on_retry_only: false
```

only when a full performance trace for every phase is genuinely needed; it can add runtime overhead.
