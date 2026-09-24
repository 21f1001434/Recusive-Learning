# Live testing — Document Type single-select commit protection

## Package

Use `HIP_PORTAL_AGENTQ_DOCTYPE_SINGLE_SELECT_LIVE_READY_431_TESTS_20260720.zip` as the new baseline.

Preserve this directory from the existing installation before replacing the code:

```text
data/hip_memory/portal_brain
```

## Run

Use the same PowerShell command used for the previous UHAUL full-dummy run:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --portal-brain `
  --import-unified-kb `
  --require-unified-kb `
  --self-heal-kb `
  --revalidate-known-parent-branches `
  --kb-repair-min-confirmations 10 `
  --kb-supersede-min-confirmations 10 `
  --exploration-agent `
  --explore-parent-branches `
  --exploration-max-values-per-parent 100 `
  --section-judge `
  --require-text-judge `
  --require-vision-judge `
  --section-judge-max-repairs 5 `
  --vision-verify `
  --vision-model "gemma-3-27b-it" `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "$RUNS_DIR"
```

## Expected Source Document Type behavior

1. Name, Transaction Type, Version, Data Format Type, Status, and Description are verified.
2. Data Format Type remains exactly `XML` when Document Identifier Operation is selected.
3. Operation commits as `All conditions are satisfied`.
4. The Operation popup is settled before the identifier-row controls are used.
5. Each Attribute `Derived From` value is committed and blurred before Usage and Expression are discovered.
6. Every repeatable row is rebound after Angular rerender.
7. A transient loss of an already verified value receives one bounded same-value restore.
8. A persistent mutation stops the phase; it is never ignored.

## Evidence to inspect after the run

```text
source_document_type/doctype_kb/doctype_target_branch_execution.json
source_document_type/doctype_kb/doctype_form_state_model.json
source_document_type/phase_execution_attempts.json
runtime_self_heal/runtime_self_heal_summary.json
```

For the Operation transaction, confirm:

- `actual_value` is `All conditions are satisfied`
- `protected_state_changes` is empty after any bounded restore
- Data Format Type remains `XML`
- `conditional_child_visibility.pass` is true

## Local verification

```powershell
python -m compileall -q hip_id_agent tests
python -m pytest -q
```

Expected result:

```text
431 passed
```
