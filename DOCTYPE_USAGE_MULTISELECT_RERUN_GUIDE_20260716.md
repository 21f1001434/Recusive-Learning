# Rerun Guide — Document Type Usage Multi-Select

Use the complete package and preserve these existing resources:

- `.env`
- `config.yaml`
- `uploads`
- `golden_screenshots`
- `data/hip_memory/portal_brain`

Run the same `run-full-dummy-fill` command. No new flag is required.

Expected Source and Target Document Type behavior for every attribute row:

```text
Usage → Flow Identifier Expression + Logging + Mapping + Routing
```

The control may display `4 selected` after collapse. The runtime must additionally verify the exact four selected options from the live listbox state.

Expected evidence in each Document Type phase:

- `doctype_kb/doctype_target_branch_execution.json`
- `dom_events/dom_events.json`
- `dom_events/dom_mutations.json`
- Playwright MCP action log with unique `aria-posinset` option selectors

A successful Usage attempt should contain:

```json
{
  "field": "attribute_usage",
  "actual_value": [
    "Flow Identifier Expression",
    "Logging",
    "Mapping",
    "Routing"
  ],
  "success": true,
  "exact_verified": true
}
```
