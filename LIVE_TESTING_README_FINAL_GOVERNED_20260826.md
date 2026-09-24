# Live Testing — Final Governed HIP Platform

## 1. Install

```powershell
cd "<extracted package>\HIP_PORTAL_FINAL_GOVERNED_CERTIFIED_AGENT"
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

AutoGen is pinned to 0.7.5 for AgentChat, Core and Ext.

## 2. Start the full stack

```powershell
.\RUN_FULL_STACK.ps1 -InstallDependencies
```

Open `http://127.0.0.1:8501`.

## 3. Learn/certify HIP first

Use **Deep Learn ALL HIP + Certify**, or:

```powershell
.\RUN_LEARN_HIP_FULL_DEEP.ps1 `
  -RunsDir "C:\hip_runs" `
  -InputJson ".\examples\uhaul_poasn_full_dummy_input.json"
```

Complete Dell SSO once in the shared Chrome session.

## 4. Preview a governed mutation

A production mutation should first be previewed:

```powershell
$env:HIP_OPERATOR_ROLE = "technical"

python -m hip_id_agent.cli preview-governed-change `
  'Search data map "MAP_A", expand it, then Deploy' `
  --config .\config.yaml `
  --operator-role technical
```

The preview shows risk, certified-family status, observed API contracts, duplicate/idempotency status, required role and rollback guidance.

## 5. Execute a governed mutation

Real mutation requires the existing three mutation keys plus governance:

```powershell
$env:HIP_OPERATOR_ROLE = "technical"
$env:HIP_ALLOW_PORTAL_MUTATION = "YES"
# Optional when governance.require_approval_id_for_mutation=true:
$env:HIP_CHANGE_APPROVAL_ID = "CHG-12345"

.\RUN_GOVERNED_CHANGE.ps1 `
  -Task 'Search data map "MAP_A", expand it, then Deploy' `
  -RunsDir "C:\hip_runs" `
  -OperatorRole technical `
  -AllowPortalMutation `
  -Confirmation "ALLOW HIP MUTATION"
```

The agent will not automatically retry a mutation after the click has been attempted.

## 6. Duplicate protection

A successfully verified identical mutation is blocked for the configured duplicate window (default 168 hours). For an intentional repeat, preview it first and then use `-ForceRepeatMutation` / `--force-repeat-mutation`.

## 7. Audit evidence

Each governed run writes:

- `change_preview.json`
- `certified_future_task_plan.json`
- `certified_future_task_execution.json`
- `certified_future_task_causal_trace.json`
- `change_receipt.json`
- `governed_change_execution.json`

Persistent structural audit events are stored in:

`data\hip_memory\portal_brain\change_audit_ledger.jsonl`

Check the hash chain with:

```powershell
python -m hip_id_agent.cli change-audit-status --config .\config.yaml
```

## 8. First live recommendation

Run deep learning and read/draft certified tasks before enabling real mutation. For a real mutation, start with a non-destructive environment/object and verify the API evidence + receipt before expanding use.
