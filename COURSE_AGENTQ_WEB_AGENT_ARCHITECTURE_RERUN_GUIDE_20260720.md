# Rerun Guide — Course AgentQ Architecture + HIP Intelligence MCP

## 1. Replace the previous package

Extract the complete ZIP into a short local path, for example:

```powershell
C:\hip_agent\hip_portal_agentq
```

Keep or copy the existing persistent memory directory into the new package:

```text
data\hip_memory\portal_brain
```

Do not copy an interrupted run folder as the application source.

## 2. Activate the environment

```powershell
cd C:\hip_agent\hip_portal_agentq
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

No MultiOn package is required.

## 3. Verify the local HIP Intelligence MCP

```powershell
python -m hip_id_agent.hip_intelligence_mcp_server `
  --memory-dir .\data\hip_memory\portal_brain\agentq\action_trajectories
```

This command waits for MCP JSON-RPC input. Press `Ctrl+C` after confirming it starts without an import error.

For normal execution, the application starts this MCP automatically.

## 4. Strict three-MCP full dummy run

Use a short runs directory to avoid Windows/OneDrive path-length problems:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.mcp-required.windows.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json .\examples\uhaul_poasn_full_dummy_input.json `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --runs-dir C:\hip_runs
```

Complete Dell SSO in the single Chrome window when prompted.

## 5. What must appear early in the run

```text
hip_intelligence_mcp_startup.json
agentq_course_architecture_manifest.json
browser_session/browser_session_started.json
```

Expected MCP startup result:

```json
{
  "enabled": true,
  "available": true,
  "required": true
}
```

## 6. Per-phase AgentQ evidence

For Data Map and every following phase, confirm:

```text
<phase>\agentq_runtime\hierarchical_phase_plan.json
<phase>\agentq_runtime\phase_initial_web_representation.json
<phase>\agentq_runtime\action_model_plan_*.json
<phase>\agentq_runtime\action_critic_*.json
<phase>\agentq_runtime\phase_agentq_summary.json
```

A successful field should show:

```json
{
  "selected_action": "execute_bound_action",
  "destructive_actions_considered": false
}
```

and a critic result with:

```json
{
  "pass": true
}
```

## 7. Memory verification

After the run, inspect:

```text
data\hip_memory\portal_brain\agentq\action_trajectories\manifest.json
data\hip_memory\portal_brain\agentq\action_trajectories\trajectories.jsonl
data\hip_memory\portal_brain\agentq\action_trajectories\preference_pairs.jsonl
```

The records must contain structural fingerprints and action outcomes but not the literal values from `input.json`.

## 8. Expected failure behavior

- Lost Create/Wizard surface → stop or safe re-establishment; never bind listing controls.
- Ambiguous binding → stop action; do not lower confidence thresholds.
- Stale overlay on an unsaved form → in-place safe recovery; never refresh the form.
- Model/judge contradiction unsupported by exact DOM → reconcile without browser replay.
- Real exact-state mismatch → fail closed with evidence.
- Completed phase → locked against judge-triggered refill.

## 9. Upload the next run

Upload the generated `UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip` together with terminal output. The high-value files are:

```text
hip_intelligence_mcp_startup.json
agentq_course_architecture_manifest.json
full_dummy_fill_summary.json
runtime_self_heal/runtime_self_heal_summary.json
<phase>/phase_execution_attempts.json
<phase>/phase_exact_state_lock.json
<phase>/agentq_runtime/phase_agentq_summary.json
<phase>/agentq_runtime/action_model_plan_*.json
<phase>/agentq_runtime/action_critic_*.json
```
