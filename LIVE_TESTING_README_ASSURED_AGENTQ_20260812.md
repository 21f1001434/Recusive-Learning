# Live testing — Assured AgentQ full E2E

## Recommended first run

Use capture mode. It fills and verifies the UI, captures all observed form APIs and payload/response evidence, and intercepts mutating submit requests before backend delivery.

```powershell
.\venv\Scripts\Activate.ps1

.\RUN_ASSURED_AGENTQ_FULL_E2E.ps1 `
  -RunsDir "C:\hip_runs" `
  -ApiMode capture
```

Complete Dell SSO in the Chrome window that opens and keep that browser window open.

## Streamlit control center

```powershell
.\RUN_STREAMLIT_ASSURED_AGENTQ.ps1 -InstallDependencies -Port 8501
```

Open `http://localhost:8501`.

The UI contains:

- Preflight
- Mission
- API payloads & responses
- UI/API crosswalk
- Assurance & causality
- Evidence
- Console

The mission cannot start when the static input/golden/upload preflight fails.

## What must pass before a phase is learned

1. Parent-child dependency execution contract.
2. Exact UI field state.
3. Sequential repeatable-row verification.
4. Deterministic section verification.
5. Dell AIA text judge.
6. Gemma vision judge and golden comparison.
7. Maximum-observability input/control coverage.
8. Required form API request/response evidence.
9. Fresh Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP quorum.
10. Validated deterministic trajectory and parent-child fingerprint.
11. Phase mission assurance certificate.

Only after this does Portal Brain/Flow Pattern Memory receive the validated structural path.

## Key evidence files per successful phase

- `phase_mission_assurance.json`
- `mcp_evidence_quorum.json`
- `validated_deterministic_trajectory.json`
- `parent_child_execution_contract.json` / state-graph dependency contract
- `maximum_observability/...`
- `form_api_intelligence/.../form_api_transactions.json`
- `form_api_intelligence/.../form_api_payload_response_bundle.json`
- `form_api_intelligence/.../ui_api_causal_trace.json`
- `form_api_intelligence/.../redacted_network.har.json`
- `form_api_intelligence/.../api_error_ledger.json`
- `form_api_intelligence/.../ui_api_input_crosswalk.json`
- `form_api_intelligence/.../observed_openapi.json`
- `form_api_intelligence/.../postman_collection.json`

## Self-heal behavior

The agent exploits a validated trajectory first. When a selector, child-mount condition, page fingerprint, API contract, judge result, or MCP evidence changes, it returns to exploration, captures new DOM/accessibility/network/console/vision evidence, repairs the earliest unresolved dependency, verifies the state, and only then updates memory.

In until-complete mode, numeric retry limits do not terminate a recoverable phase. Proven unsafe/mutating actions remain blocked, and external outages can be stopped manually with `Ctrl+C`.

## API write mode

Do not use this for evidence-only testing. Real API mutation requires both:

```powershell
$env:HIP_ALLOW_API_MUTATION = "YES"
```

and:

```powershell
.\RUN_ASSURED_AGENTQ_FULL_E2E.ps1 `
  -RunsDir "C:\hip_runs" `
  -ApiMode write `
  -AllowApiMutation
```

The runtime replays only an API request actually observed from the authenticated UI; LLMs cannot invent endpoints, methods, payload keys or authorization.
