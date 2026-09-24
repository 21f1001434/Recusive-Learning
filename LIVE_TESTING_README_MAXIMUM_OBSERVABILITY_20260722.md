# Live Testing — Maximum-Observability Autonomous Mission

## Run

```powershell
.\RUN_MAXIMUM_OBSERVABILITY_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs"
```

For additional raw evidence:

```powershell
.\RUN_MAXIMUM_OBSERVABILITY_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -WriteHeavyEvidence
```

## Important

1. Run from the extracted project directory.
2. Activate the same Python virtual environment used for the prior successful Data Map and Document Type runs.
3. Complete Dell SSO in the opened Chrome window.
4. Keep the same Chrome window open.
5. Do not manually alter fields while the autonomous transaction is running.
6. Stop with `Ctrl+C` only for a deliberate external outage.

## Evidence locations

Failure evidence:

```text
<run>\runtime_self_heal\<phase>\attempt_<N>_<classification>\
```

Maximum-observability failure evidence:

```text
...\maximum_observability\maximum_page_intelligence.json
...\maximum_observability\sanitized_dom_structure.html
...\maximum_observability\input_control_coverage.json
...\maximum_observability\deterministic_replay_readiness.json
```

Successful phase evidence:

```text
<run>\<phase>\maximum_observability\attempt_<N>_success\
```

## Completion rule

The phase must pass exact state verification, dependency order, sequential row verification, text judge, vision judge, golden-image comparison, input/control coverage and replay-readiness evidence before its trajectory can be promoted.
