# Certified Future Task Agent — Live Run Guide

## Before running

Use the Full HIP deep-learning/certification mission at least once in the Dell tenant so the persistent capability graph contains current page/actions/form/API/replay evidence.

Activate the environment and install requirements:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

## Plan only

```powershell
python -m hip_id_agent.cli plan-certified-task `
  'Search data map "MAP_A", expand it, then Edit' `
  --config .\config.yaml
```

A plan reports family certification, execution mode, unresolved steps, mutation risk and whether guarded adaptive exploration may be required.

## Execute read/draft task

```powershell
.\RUN_CERTIFIED_FUTURE_TASK.ps1 `
  -Task 'Search data map "MAP_A", expand it, then Edit' `
  -RunsDir 'C:\hip_runs'
```

The runtime attempts: certified task replay → verified family replay → learned semantic capability → adaptive DOM/MCP recovery.

## Cross-family example

```powershell
python -m hip_id_agent.cli run-certified-task `
  'Search data map "MAP_A", expand it, then Edit; then search rule "RULE_A", expand it, then View' `
  --config .\config.yaml `
  --runs-dir C:\hip_runs `
  --adaptive
```

## Mutation task

Mutation actions require all three gates:

```powershell
$env:HIP_ALLOW_PORTAL_MUTATION = 'YES'

.\RUN_CERTIFIED_FUTURE_TASK.ps1 `
  -Task 'Search data map "MAP_A", expand it, then Deploy' `
  -RunsDir 'C:\hip_runs' `
  -AllowPortalMutation `
  -Confirmation 'ALLOW HIP MUTATION'
```

If a mutation click is invoked and the result becomes ambiguous, the agent stops and does **not** auto-retry.

## Evidence

Each run writes:

- `autogen_preflight.json`
- `certified_future_task_plan.json`
- `certification_preflight.json`
- `certified_mutation_gate.json`
- `certified_future_task_execution.json`
- `certified_future_task_causal_trace.json`
- MCP action snapshots / runtime evidence
- redacted browser/network logs

Successful tasks are promoted to value-free cross-family task replay memory.
