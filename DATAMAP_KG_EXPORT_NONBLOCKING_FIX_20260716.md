# Data Map Knowledge Graph Export Non-Blocking Fix

## Live failure

Run `UHAUL-POASN-FULL-DUMMY-20260716-191407` completed Data Map form filling and exact state-graph reconciliation, then failed while exporting the compact Data Map API-flow Knowledge Graph.

The failing statement supplied `order` twice:

```python
add_node(..., order=idx, **fill)
```

The runtime fill record already contained its own `order` property. Python rejected the call before `add_node()` could normalize the record:

```text
TypeError: ...add_node() got multiple values for keyword argument 'order'
```

This was a reporting/serialization defect. It was not a portal fill, MCP, SSO, judge, DOM-event, or Knowledge Graph learning failure.

## Implementation

1. Added collision-safe `_kg_record_props(record, graph_order)` normalization.
2. Preserved each source record's existing `order` value.
3. Added a separate `graph_order` property for deterministic visualization order.
4. Applied the normalization to:
   - pagination replay audit records;
   - detail API attempts;
   - dummy-fill/state-graph execution records.
5. Added `_write_datamap_api_flow_knowledge_graph_safe()`.
6. Knowledge Graph reporting errors now generate:

```text
datamap_api_flow_knowledge_graph_export_status.json
```

with `status=warning` and `non_blocking=true`, while the verified portal phase continues.
7. Exact form verification and no-mutation safety gates remain blocking and unchanged.

## Verification

- Python compilation: passed.
- Data Map targeted tests: 15 passed.
- Complete automated suite: 290 passed.
- Duplicate `order` regression: passed.
- Non-blocking export regression: passed.

## Expected next run

The Data Map stage should write either:

```json
{"status": "ok", "pass": true}
```

or, only if a future reporting serializer problem occurs:

```json
{"status": "warning", "pass": false, "non_blocking": true}
```

In both cases, if exact Data Map form verification passed, the workflow proceeds to Source Document Type.
