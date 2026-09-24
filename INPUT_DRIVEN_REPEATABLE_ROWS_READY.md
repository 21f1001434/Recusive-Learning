# Input-driven repeatable row replication ready

## What changed

The full dummy-fill E2E runner now learns repeatable sections directly from `input.json` and records/clicks row-level `+ Add` buttons before filling.

This covers sections such as:

- Document Type `attributes_to_configure` rows
- Rule `conditions.rows`
- Rule `actions` lists, when provided as lists
- BizFlow `flow_identifiers.conditions`
- BizFlow `process_steps`
- BizFlow `process_steps[].configuration.file_name_parts`
- BizFlow `configure_routing.conditions.rows`
- BizFlow routing actions, when provided as lists
- Transport Profile parameter/document-type detail lists, when present
- Data Map cross-reference map lists, when present

## Safety

Only row-level `+ Add`/`Add` controls are clicked. The blocker still prevents Save/Create/Submit/Delete/Deploy/Enable/Disable/Publish/Update.

## Output folder

`config.yaml`, `config.example.yaml`, and `config.mcp-required.windows.yaml` now default to:

```text
C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs
```

## New evidence saved per phase

Each phase KB now stores:

- `repeatable_section_plan`
- `repeatable_row_audit`

Each fast replay blueprint stores:

- `repeatable_section_plan`
- Add-click count derived from input row count
- row values from input.json
- tab/section aliases for locating the correct Add button

## Validation

`python -m pytest -q` result: `194 passed`.
