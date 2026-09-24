# Live Testing: All HIP Phases Until Complete

## Preserve existing learning

Before replacing the package, copy your current validated directory:

```powershell
Copy-Item -Recurse -Force .\data\hip_memory\portal_brain C:\hip_backup\portal_brain
```

After extracting the new package, restore it to:

```text
data\hip_memory\portal_brain
```

## Recommended command

```powershell
.\RUN_ALL_PHASES_UNTIL_COMPLETE.ps1 -RunsDir "C:\hip_runs"
```

For maximum raw evidence in addition to the structured forensic bundle:

```powershell
.\RUN_ALL_PHASES_UNTIL_COMPLETE.ps1 `
  -RunsDir "C:\hip_runs" `
  -WriteHeavyEvidence
```

The structured forensic mode is enabled by default and is normally sufficient. Heavy evidence can create much larger run directories during repeated retries.

## Phase order

```text
Data Map
→ Source Document Type
→ Target Document Type
→ Rule
→ Source Transport Profile
→ Target Transport Profile
→ BizFlow
```

Each phase must independently pass before the next phase starts.

## What is learned

For every successful phase, inspect:

```text
<phase>\validated_deterministic_trajectory.json
<phase>\flow_pattern_memory_promotion.json
<phase>\validated_replay_promotion_summary.json
<phase>\portal_learning\attempt_*\portal_learning_summary.json
```

For every failed attempt, inspect:

```text
runtime_self_heal\<phase>\attempt_*\failure_evidence.json
runtime_self_heal\<phase>\attempt_*\forensic_evidence_manifest.json
runtime_self_heal\<phase>\attempt_*\local_form_state.json
runtime_self_heal\<phase>\attempt_*\playwright_mcp_dom.json
runtime_self_heal\<phase>\attempt_*\playwright_mcp_network.json
runtime_self_heal\<phase>\attempt_*\playwright_mcp_console.json
runtime_self_heal\<phase>\attempt_*\chrome_devtools_mcp_dom.json
runtime_self_heal\<phase>\attempt_*\chrome_devtools_mcp_network.json
runtime_self_heal\<phase>\attempt_*\chrome_devtools_mcp_console.json
runtime_self_heal\<phase>\attempt_*\browser_event_history.json
runtime_self_heal\<phase>\attempt_*\self_heal_decision.json
```

## Completion

A phase is complete only when deterministic verification, text judge, vision judge and golden-state comparison all pass. The final run summary must show all seven phases completed.

## Manual interruption

The until-complete mode intentionally has no retry count limit. Use `Ctrl+C` when an external dependency is deliberately unavailable, such as Dell SSO, HIP portal, MCP, network or Dell AIA.
