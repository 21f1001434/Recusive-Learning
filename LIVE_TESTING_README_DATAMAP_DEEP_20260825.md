# Live Testing — Data Maps Deep Learning

## Recommended run
From the extracted project root in PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
.\RUN_LEARN_DATAMAPS_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Complete Dell SSO in the opened shared Chrome session and keep the browser open.

## Streamlit
Start the backend/frontend stack:

```powershell
.\RUN_FULL_STACK.ps1 -InstallDependencies
```

Open `http://127.0.0.1:8501`, select **Learn HIP**, then click **Deep Learn Data Maps**.

## Expected learning sequence
1. Open Data Maps listing.
2. Capture initial DOM/ARIA/API state.
3. Learn search control.
4. Inspect listing filter controls and option sets without changing filter state.
5. Learn pagination and safely validate Next/Previous when available.
6. Search the Data Map from the current input JSON.
7. Expand the exact matching row.
8. Inventory every revealed row action.
9. Re-open the target row before each action inspection.
10. Inspect Edit/Clone/View/History/Audit surfaces safely and close without save.
11. Probe Migrate/Deploy/Delete behind the mutation request-abort barrier.
12. Capture action-specific APIs, payload shapes, responses/read calls, prerequisites and confirmation surfaces.
13. Persist capability relations and verified deterministic replay profiles.

## Key run evidence
`<run>/datamap_deep_discovery_summary.json`

Deep evidence is under:
`<run>/deep_discovery/data_maps/`

Typical folders/files include:
- `initial/`
- `filters/`
- `pagination/`
- `draft_actions/edit/`
- `draft_actions/clone/`
- `mutation_probes/migrate/`
- `mutation_probes/deploy/`
- `mutation_probes/delete/`
- `api_transactions_*.json`
- form contracts
- maximum-observability snapshots
- AgentQ representations/critics

Persistent knowledge:
`data/hip_memory/portal_brain/capability_graph.json`

## Safety expectation
During Migrate/Deploy/Delete discovery, any mutating request must show `blocked_before_backend: true`. The discovery command must never be used to perform a real portal mutation.

## Future replay test
After a successful deep learning run:

```powershell
python -m hip_id_agent.cli plan-future-task `
  'Search data map "YOUR_MAP", expand it, then Edit' `
  --config .\config.yaml
```

A verified profile should allow the plan to report `execution_mode: deterministic_replay`.
