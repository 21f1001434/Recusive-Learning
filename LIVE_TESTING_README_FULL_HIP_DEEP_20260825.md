# Live Testing — Full HIP Deep Learning + Certification

## Recommended launch

```powershell
cd "<extracted package>"
.\venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
.\RUN_LEARN_HIP_FULL_DEEP.ps1 -RunsDir "C:\hip_runs"
```

Complete Dell SSO when Chrome opens. Do not delete the configured persistent browser profile between family learners; the mission reuses it so later families normally do not require another login.

## Direct CLI

```powershell
python -m hip_id_agent.cli learn-hip-full-deep `
  --config .\config.yaml `
  --input-json .\examples\uhaul_poasn_full_dummy_input.json `
  --customer HIP-FULL-DEEP-LEARNING `
  --runs-dir C:\hip_runs `
  --require-mcp `
  --continue-on-family-failure
```

## Resume an interrupted run

```powershell
.\RUN_LEARN_HIP_FULL_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -ResumeRun "C:\hip_runs\<prior-run-id>"
```

## Full stack UI

```powershell
.\RUN_FULL_STACK.ps1 -InstallDependencies
```

Open `http://127.0.0.1:8501`, then use **Deep Learn ALL HIP + Certify**.

## What to inspect

At the end of the run, inspect:

- `full_hip_deep_learning_summary.json`
- `hip_capability_certification.json`
- `hip_capability_gap_queue.json`
- `hip_global_api_catalog.json`
- `hip_replay_registry.json`
- `hip_capability_inventory.json`

`operational_readiness=true` means all five family core gates passed. `full_visible_action_coverage=false` is allowed when a tenant/role does not expose one or more expected row actions; those actions appear in the gap queue rather than being silently ignored.

## Safety behavior

This is a learning/certification mission. It preserves the safe mutation-probe barrier used by each deep learner. Do not use this mission to intentionally Deploy/Delete/Migrate real portal entities.
