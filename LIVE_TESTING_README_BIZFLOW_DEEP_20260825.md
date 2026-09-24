# Live Testing — BizFlow Deep Learning

## PowerShell
```powershell
.\venv\Scripts\Activate.ps1

.\RUN_LEARN_BIZFLOWS_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Or directly:
```powershell
python -m hip_id_agent.cli learn-bizflows-deep `
  --config ".\config.yaml" `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --customer "HIP-BIZFLOW-DEEP-DISCOVERY" `
  --runs-dir "C:\hip_runs" `
  --require-mcp
```

Complete Dell SSO once in the shared Chrome session.

## Expected learning sequence
Manage Biz Flow listing -> filters/pagination -> search current BizFlow -> expand exact row -> inspect safe actions -> safe mutation prerequisite probes -> + Add -> B2B-Flow-PubSub-Template -> Create Biz Flow -> Flow Details -> Configure Source -> Flow Identifiers -> Configure Target(s) -> Process Steps -> Configure Routing -> close without Save/Create/Submit.

## Primary output
`<run>\bizflow_deep_discovery_summary.json`

Detailed evidence:
`<run>\deep_discovery\bizflows\`

Persistent value-free capability memory:
`data\hip_memory\portal_brain\capability_graph.json`

## Important safety behavior
The mission may open draft/navigation surfaces and fill the unsaved BizFlow form. It does not execute final Save/Create/Submit. Migrate/Deploy/Delete probes use a network-abort barrier and are not delivered to the HIP backend.
