# Live Testing — Rules Conditions Click-Commit Build

## Preserve validated memory

Before replacing the current baseline, preserve or merge:

```text
data\hip_memory\portal_brain
```

Do not copy stale run output folders over the new source code.

## Recommended PowerShell run

```powershell
$RUNS_DIR = "C:\hip_runs"

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

## What to verify on the Rules screen

The terminal and run evidence should show one successful Conditions add transaction:

```text
row_count_before: 1
row_count_after: 2
exact_plus_one: true
successful_physical_click_attempt: 1 or 2
```

The final form must visibly contain two separate rows:

1. `Attributes | Equals | uhaul | Receiver`
2. `Attributes | Contains | DELL | Sender`

The run must stop before Actions when any of these occurs:

- no `click` event reaches Create Condition;
- the row count remains 1 after the bounded retry;
- two input rows resolve to the same physical row identity;
- an existing row changes during retry;
- the row count jumps by more than one.

## Evidence to share after the run

Share the new run ZIP and terminal output. The most useful files are:

```text
rule\rule_kb\rule_target_branch_execution.json
rule\dom_events\dom_events.json
rule\dom_snapshots\runtime_self_heal_rule_*_exact_value_mismatch.html
rule\phase_execution_attempts.json
```
