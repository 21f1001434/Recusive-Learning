# Live testing — Transport Profiles Deep Learning

## Recommended command
```powershell
.\venv\Scripts\Activate.ps1
.\RUN_LEARN_TRANSPORT_PROFILES_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Direct CLI:
```powershell
python -m hip_id_agent.cli learn-transport-profiles-deep `
  --config ".\config.yaml" `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --customer "HIP-TP-DEEP-DISCOVERY" `
  --runs-dir "C:\hip_runs" `
  --require-mcp
```

Complete Dell SSO in the shared Chrome session. The mission studies both Source and Target Transport Profiles. It does not click Save/Create/Submit.

## Main outputs
- `<run>\transport_profile_deep_discovery_summary.json`
- `<run>\deep_discovery\transport_profiles\source_transport_profile\...`
- `<run>\deep_discovery\transport_profiles\target_transport_profile\...`
- `parent_child_dependency_blueprint.json`
- `stateful_form_execution.structural.json`
- `api\api_transactions_*.json`
- `api\ui_api_causal_trace.json`
- persistent `data\hip_memory\portal_brain\capability_graph.json`

## Expected replay profiles
- `create_source_transport_profile`
- `create_target_transport_profile`

A replay profile is verified only when the exact stateful execution passes. Current values continue to come from the runtime `input.json`.
