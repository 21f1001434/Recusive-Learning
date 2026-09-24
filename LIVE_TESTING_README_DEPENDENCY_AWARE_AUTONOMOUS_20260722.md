# Live Testing — Dependency-Aware Autonomous Mission

## 1. Preserve learned memory

Before replacing an older package, retain:

```text
data\hip_memory\portal_brain
```

Copy that directory into the same location in the new extracted project.

## 2. Activate the environment

Open PowerShell in the extracted project root:

```powershell
cd "C:\path\to\HIP_PORTAL_AGENTQ_COURSE_ARCHITECTURE_MCP_FIX"
.\venv\Scripts\Activate.ps1
```

Install requirements when using a new environment:

```powershell
uv pip install -r requirements.txt
```

## 3. Run the autonomous mission

```powershell
.\RUN_DEPENDENCY_AWARE_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs"
```

For the maximum raw evidence bundle:

```powershell
.\RUN_DEPENDENCY_AWARE_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -WriteHeavyEvidence
```

## 4. Complete Dell SSO once

When Chrome opens, complete Dell SSO. Keep that Chrome window open. The same browser context is reused for every phase.

## 5. Expected behavior

For a new or drifted form, the console should indicate exploration. The agent will:

1. Read the current phase object from `input.json`.
2. Compile the parent–child dependency contract.
3. Execute only ready parent nodes.
4. Wait for child controls and owned DDS option lists.
5. Rebind after Angular rerenders.
6. Complete repeatable rows sequentially.
7. Verify every field and protected prior value.
8. Compare the filled state with the relevant golden image.
9. Run deterministic, text and vision judges.
10. Promote the path only after all required gates pass.

For a validated matching form, the console should indicate deterministic or exploitation replay. The agent will reuse the learned selector/event/wait path and explore only when live drift is detected.

## 6. Evidence to share after a failure

Zip and share the entire run directory. The most useful files are:

```text
<run>\mission_state.json
<run>\mission_parent_child_contract.json
<run>\<phase>\parent_child_execution_contract.json
<run>\<phase>\phase_execution_attempts.json
<run>\runtime_self_heal\<phase>\attempt_*\dependency_scheduler_state.json
<run>\runtime_self_heal\<phase>\attempt_*\failure_evidence.json
<run>\runtime_self_heal\<phase>\attempt_*\forensic_evidence_manifest.json
```

The dependency evidence identifies the earliest unresolved parent, the child waiting for it, the latest exact state and the action that must not be repeated.

## 7. Stop conditions

The self-heal loop continues until the phase passes, but press `Ctrl+C` for an intentional external outage such as unavailable SSO, network, MCP servers, Dell AIA or HIP Portal maintenance.

A dependency cycle, unsafe action request or broken mission dependency contract stops fail-closed automatically.

## 8. Completion definition

A phase is complete only when:

- Every required current-input field is exact.
- Parent–child and repeated-row contracts pass.
- One-to-one live control binding passes.
- No completed field was unintentionally changed.
- The exact-state lock is written.
- Text judge passes.
- Vision judge and golden-state comparison pass.

The mission is complete only after all seven phases pass these gates.

## 9. No-save scope

This package fills and verifies the forms but intentionally does not click Save/Create/Submit/Delete/Deploy or other final mutation controls.
