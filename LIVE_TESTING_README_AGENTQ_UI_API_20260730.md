# Live Testing — AgentQ UI/API Autonomous HIP Mission

## 1. Preserve learned memory

Before replacing an older package, preserve:

```text
data\hip_memory\portal_brain
```

Copy it into the same location in the extracted new package.

## 2. Activate the existing environment

```powershell
cd "C:\path\to\HIP_PORTAL_AGENTQ_COURSE_ARCHITECTURE_MCP_FIX"
.\venv\Scripts\Activate.ps1
```

The package does not require a second browser controller. Keep the single Chrome session opened by the agent and complete Dell SSO there.

## 3. Recommended first run: UI + API capture, no backend write

```powershell
.\RUN_AGENTQ_UI_API_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -ApiMode capture `
  -WriteHeavyEvidence
```

This fills every form through BizFlow, captures API traffic and exact submit payloads, but aborts all UI mutation requests before delivery.

## 4. Resume an interrupted mission

```powershell
.\RUN_AGENTQ_UI_API_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -ResumeRunDir "C:\hip_runs\<run-folder>" `
  -ApiMode capture
```

Only phases with exact locks, judge passes and complete API evidence are adopted. Unproven phases execute again.

## 5. Validation-evidence mode

```powershell
.\RUN_AGENTQ_UI_API_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -ApiMode validate
```

The agent reports validation endpoints and observed UI validation results without sending a reconstructed request.

## 6. Explicit API write mode

Use only when creating/updating the HIP objects through API is intended and approved:

```powershell
$env:HIP_ALLOW_API_MUTATION = "YES"

.\RUN_AGENTQ_UI_API_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -ApiMode write `
  -AllowApiMutation
```

Both confirmations are required. The request is the exact request captured from the UI in the same process and authenticated browser context. A saved redacted payload is never used as the write body.

## 7. Per-phase API artifacts

Look under each successful phase:

```text
<phase>\form_api_intelligence\attempt_<N>\
```

Important files:

```text
form_open_api_contracts.json
ui_fill_api_contracts.json
phase_api_catalog.json
ui_api_input_crosswalk.json
observed_openapi.json
postman_collection.json
blocked_submit_api_capture.json
api_execution_result.json
form_api_agentq_result.json
```

## 8. Evidence for a failure

```text
runtime_self_heal\<phase>\attempt_<N>_<failure>\
```

The bundle includes DOM/Angular state, accessibility, Playwright MCP and Chrome DevTools MCP network/console evidence, mutation/event timelines, screenshots, golden differences, dependency status, API contracts, input/UI/API crosswalk and the next safe recovery target.

## 9. Completion contract

A phase completes only when:

- every required current-input path maps to a UI control;
- all parent-child gates are ordered and committed;
- repeated rows have exact unique identities;
- exact DOM verification passes;
- deterministic, text and vision judges pass;
- golden-state comparison passes;
- API evidence exists when required;
- API execution result matches the selected mode;
- deterministic trajectory and evidence lock are persisted.

## 10. Stop conditions

In until-complete mode the agent continues safe exploration/exploitation without normal retry ceilings. Use `Ctrl+C` for an intentional SSO, MCP, network, Dell AIA or portal outage. Proven safety violations still fail closed.
