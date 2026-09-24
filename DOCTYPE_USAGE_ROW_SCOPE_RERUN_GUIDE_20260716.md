# Rerun guide — Document Type Usage row scope

1. Use the complete corrected package or replace the two hotfix source files.
2. Preserve `.env`, `config.yaml`, `uploads`, `golden_screenshots`, and `data/hip_memory/portal_brain`.
3. Run the same `run-full-dummy-fill` command. No new CLI flag is required.
4. Complete Dell SSO when Chrome opens.

For each Source and Target Document Type attribute row, the state-graph record should contain:

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
  "exact_verified": true,
  "reason": "specialized filler committed exact value"
}
```

It must not contain `4 selected` in `actual_value`, and a Usage attempt selector must never be the selector previously used to type Attribute Name.

If the run stops, share the complete newest run ZIP.
