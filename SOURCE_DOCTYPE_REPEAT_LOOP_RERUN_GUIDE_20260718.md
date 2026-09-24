# Rerun Guide — Repeated Source Document Type Fix

Use the same `run-full-dummy-fill` command.

Expected behavior:

1. Complete Dell SSO once.
2. Data Map runs once unless a genuine exact-state repair is required.
3. Source Document Type fills once.
4. Optional KG/report generation occurs after exact form completion.
5. A reporting warning does not reopen Source Document Type.
6. Text and vision judges evaluate the already-filled form.
7. Execution proceeds to Target Document Type, Rule, Source TP, Target TP and BizFlow.

Expected Source Document Type artifacts:

```text
source_document_type/doctype_kb/doctype_target_branch_execution.json
source_document_type/doctype_kb/doctype_api_flow_knowledge_graph_export_status.json
source_document_type/phase_execution_attempts.json
```

When a reporting exporter fails after exact form completion, also expect:

```text
source_document_type/reporting_failure_recovered_without_replay.json
```

That artifact must contain:

```json
{
  "phase_replay_required": false,
  "browser_repair_executed": false,
  "next_step": "build verification and run independent judges"
}
```

A real form mismatch still enters bounded self-heal and may replay only the
affected phase. Reporting-only failures cannot replay the phase.
