# Live Testing — HIP Browser Intelligence Platform

## 1. Install dependencies

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

Verify AutoGen 0.7.5:

```powershell
python -c "from hip_id_agent.autogen_runtime import assert_autogen_075; print(assert_autogen_075())"
```

## 2. Start full backend/frontend stack

```powershell
.\RUN_FULL_STACK.ps1 -InstallDependencies
```

Frontend: `http://127.0.0.1:8501`

Backend: `http://127.0.0.1:8000`

## 3. Learn HIP

From Streamlit open **Learn HIP** and click **Start Learn HIP**.

Or run directly:

```powershell
.\RUN_LEARN_HIP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Complete Dell SSO in the opened Chrome window and keep that Chrome session open.

Expected discovery families:

- data_maps
- document_types
- rules
- transport_profiles
- bizflows

Persistent capability graph:

`data\hip_memory\portal_brain\capability_graph.json`

Run evidence:

`C:\hip_runs\HIP-PORTAL-DISCOVERY-<timestamp>\discovery\<family>\`

Important evidence includes:

- `surface.json`
- `maximum_observability\maximum_page_intelligence.json`
- `api_transactions_form_open.json`
- `api_transactions_search.json`
- `api_transactions_expand_row.json`
- safe inspected Edit/View/Details surface when found
- `discovery_summary.json`

## 4. Inspect learned capabilities

```powershell
python -m hip_id_agent.cli capability-status --config .\config.yaml --page-family data_maps
```

Or use Streamlit **Portal Knowledge**.

## 5. Plan a future task

```powershell
python -m hip_id_agent.cli plan-future-task `
  'Search data map "DELLCoXMLASNXX08C_U-HAUL", expand it, then Edit' `
  --config .\config.yaml
```

The plan must reference only learned `cap-*` IDs.

## 6. Execute a non-mutating/draft future task

```powershell
python -m hip_id_agent.cli run-future-task `
  'Search data map "DELLCoXMLASNXX08C_U-HAUL", expand it, then Edit' `
  --config .\config.yaml `
  --runs-dir "C:\hip_runs"
```

## 7. Mutation-grade future task

Only when specifically approved:

```powershell
$env:HIP_ALLOW_PORTAL_MUTATION = "YES"

python -m hip_id_agent.cli run-future-task `
  'Search data map "MAP_A", expand it, then Deploy' `
  --config .\config.yaml `
  --runs-dir "C:\hip_runs" `
  --allow-portal-mutation `
  --confirmation "ALLOW HIP MUTATION"
```

All three gates are required. Discovery itself never enables this authorization.

## 8. Continue normal configuration mission

The existing assured AutoGen 0.7.5 seven-phase runner remains supported. After a phase passes mission assurance, its validated form trajectory is also promoted into the capability graph. This lets future tasks reuse form-field and parent-child knowledge learned during configuration.
