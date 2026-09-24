# Stateful HIP Portal no-save rerun guide

## 1. Replace the current project

Extract the complete ZIP into a new folder. Preserve your external `.env` and `data\hip_memory\portal_brain` directory. Do not copy secrets into the package.

## 2. Verify locally

```powershell
python -m compileall .\hip_id_agent
pytest -q
python -m hip_id_agent.cli vision-preflight --config .\config.yaml
```

Expected local result: `279 passed` and a successful multimodal capability preflight.

## 3. Run the controlled no-save workflow

Use the same `run-full-dummy-fill` command. Complete Dell SSO manually when requested. Keep all mutation restrictions enabled.

For faster validation after the first successful learning pass, reduce exploratory breadth during replay:

```powershell
--exploration-max-values-per-parent 30
```

The first learning run may use a larger value only when exhaustive branch discovery is needed.

## 4. Required new evidence

For each phase inspect:

- `*target_branch_execution.json`
- `portal_form_knowledge/*target_branch_form_knowledge.json`
- `deterministic_plans/*_deterministic_plan.json`
- `fast_replay_blueprints/*_fast_fill_blueprint.json`
- section judge JSON and screenshot
- repeatable-row audit
- Playwright MCP action log
- dual-MCP same-surface report

For BizFlow also inspect `bizflow_state_graph_*.json` for every tab and routing restoration.

## 5. Expected Source Document Type behavior

The next run must:

1. Create exactly one Document Identifier row and five Attribute rows.
2. Select `TRANSACTION_ROOT_ELEMENT`, then fill `DellAutoASN` in the newly revealed Value control.
3. Select each Attribute `Derived From` parent.
4. Rediscover and fill each revealed Expression/Value control.
5. Select Usage items individually, not as one comma-separated option.
6. Verify the exact selected set and expressions before exploration.
7. Explore alternate parent values only after the target branch passes.
8. Restore and rejudge the target branch.

## 6. Safety

Never enable Save, final Create, Submit, Delete, Remove, Deploy, Publish, Update, Enable/Disable or Confirm during this verification run.
