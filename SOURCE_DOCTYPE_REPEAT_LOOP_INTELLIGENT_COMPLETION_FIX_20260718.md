# Source Document Type Repeated-Fill Loop — Root Cause and Fix

## Supplied interrupted run

Run: `UHAUL-POASN-FULL-DUMMY-20260718-002105`

The single persistent browser session worked:

- Chrome started once.
- SSO was completed once.
- Data Map completed.
- Source Document Type reached the exact requested live form state.
- The user stopped the run while Source Document Type was being replayed for the third time.

## Root cause

The browser/form executor was not failing. The authoritative artifact
`source_document_type/doctype_kb/doctype_target_branch_execution.json`
reported:

- `pass: true`
- `status: pass`
- all expected field attempts exact-verified

After this success, the optional Document Type Knowledge Graph exporter crashed:

```text
build_doctype_api_flow_knowledge_graph.<locals>.add_node()
got multiple values for keyword argument 'order'
```

The exporter accepted `order` as an explicit keyword while the captured audit
record also contained `order`. The real run then exposed a second latent
collision: captured fill records can also contain `node_id`.

The runtime self-heal classifier correctly called this a reporting-only failure,
but its old recovery action was `refresh_evidence_and_reopen`. That reopened the
unsaved Create Document Type form and repeated all filling.

## Corrections

### 1. Duplicate-safe Knowledge Graph node API

KG identity parameters are now positional-only. Captured properties may safely
contain:

- `node_id`
- `node_type`
- `display_label`
- `order`
- `id`
- `type`
- `label`

Audit properties are merged into one dictionary before expansion.

The same correction is applied to:

- Document Type KG
- Rule KG
- Transport Profile KG
- Generic run/action/click KG
- HTML report render context

### 2. Reporting exporters are non-blocking

Document Type, Rule, Transport Profile and BizFlow reporting/KG exports now use
safe wrappers. A serializer failure writes an explicit warning artifact but does
not invalidate the live portal phase.

### 3. Exact-completion checkpoint

Before replaying a phase after a reporting exception, the orchestrator checks the
phase-specific authoritative exact-execution artifact:

- Data Map: `datamap_target_branch_execution.json`
- Document Type: `doctype_target_branch_execution.json`
- Rule: `rule_target_branch_execution.json`
- Transport Profile: `transport_profile_target_branch_execution.json`
- BizFlow: stateful target execution in the BizFlow form KB

When this checkpoint passes, the agent:

1. Does not close/reopen the form.
2. Does not refill fields.
3. Records `reporting_failure_recovered_without_replay.json`.
4. Builds normal deterministic verification.
5. Runs Dell AIA text judge and Gemma vision judge.
6. Continues to the next phase only if independent judges pass.

### 4. Self-heal policy

`reporting_only_failure` now selects `rejudge_after_evidence_refresh`.
That action refreshes passive DOM/MCP evidence only. It does not navigate,
dismiss the form or initiate SSO.

GPT-OSS-120B remains advisory and may choose only from the safe action list.
It cannot override the exact completion checkpoint or approve its own repair.

## Exact supplied-run replay

The saved Source Document Type phase was replayed offline against the corrected
logic:

- Exact completion checkpoint: PASS
- Authoritative artifact: `doctype_target_branch_execution.json`
- Patched KG export: PASS
- KG nodes: 1,143
- KG edges: 1,365
- Phase replay required: false
- Next step: deterministic/text/vision judge, then Target Document Type

## Verification

- Python compilation: PASS
- New targeted regression suite: 58 passed
- Complete suite: 349 passed
- Supplied-run exact checkpoint replay: PASS
- Supplied-run KG export replay: PASS
- Authenticated live HIP rerun: not performed in this environment
