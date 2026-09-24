# HIP Independent Section Execution — v1.7.0

Every major HIP configuration family can be executed independently from the same governed agent.

## User-facing sections

| Section | Internal phases executed | Unrelated forms opened? |
|---|---|---|
| `data-map` | `data_map` | No |
| `source-document-type` | `source_document_type` | No |
| `target-document-type` | `target_document_type` | No |
| `document-type` | Source + Target Document Type | No |
| `rule` | `rule` | No |
| `source-transport-profile` | `source_transport_profile` | No |
| `target-transport-profile` | `target_transport_profile` | No |
| `transport-profile` | Source + Target Transport Profile | No |
| `bizflow` | `biz_flow` | No |
| `all` | all seven phases | N/A |

## Example — Transport Profile only

```powershell
python -m hip_id_agent.cli run-section transport-profile `
  --config .\config.yaml `
  --input-json .\examples\uhaul_poasn_full_dummy_input.json `
  --runs-dir C:\hip_runs `
  --require-mcp `
  --vision-verify
```

This opens/fills/verifies only Source and Target Transport Profile. Data Map, Document Type, Rule and BizFlow are not executed.

Source only:

```powershell
python -m hip_id_agent.cli run-section source-transport-profile `
  --input-json .\input.json `
  --runs-dir C:\hip_runs
```

Target only:

```powershell
python -m hip_id_agent.cli run-section target-transport-profile `
  --input-json .\input.json `
  --runs-dir C:\hip_runs
```

## Other sections

```powershell
python -m hip_id_agent.cli run-section data-map --input-json .\input.json
python -m hip_id_agent.cli run-section document-type --input-json .\input.json
python -m hip_id_agent.cli run-section source-document-type --input-json .\input.json
python -m hip_id_agent.cli run-section target-document-type --input-json .\input.json
python -m hip_id_agent.cli run-section rule --input-json .\input.json
python -m hip_id_agent.cli run-section bizflow --input-json .\input.json
```

List all aliases/sections:

```powershell
python -m hip_id_agent.cli list-sections
```

## UI

Both Streamlit surfaces expose section execution:

- `bun run ui` → **Execution scope** → choose the section → **Run selected section only**.
- `bun run platform` → **Learn HIP** → **Execute One HIP Section** → **Run Selected Section Only**.

The backend endpoint is `POST /api/section-run/start`; `GET /api/sections` returns the supported catalog.

## Isolation and dependencies

Section-only means the agent does not auto-create or execute upstream forms. If a selected form references an existing HIP object (for example a Transport Profile references a Document Type, Account, System/Partner/Application), that referenced object must already be available in the live HIP dropdown. The agent still uses the same Browser Use/CDP session, MCP stack, deterministic controls, input-driven repeatable-row engine, section judges, vision checks, AgentQ recovery and evidence collection.

The scoped input preflight validates only the selected phases. This permits a Transport-Profile-only input file containing only `objects.source_transport_profile` and/or `objects.target_transport_profile`, while still validating all provided Transport Profile fields and selected-phase control coverage.
