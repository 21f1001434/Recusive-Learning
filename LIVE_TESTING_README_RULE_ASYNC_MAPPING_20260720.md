# Live Testing — Rules Async Mapping Identifier Fix

## Replace the baseline

Extract the new ZIP into a clean folder. Preserve the validated memory directory from the previous installation:

```text
data\hip_memory\portal_brain
```

Copy that directory into the same location in the new package before starting the run.

## Recommended verification

```powershell
python -m compileall -q hip_id_agent tests
python -m pytest -q
```

Expected result:

```text
438 passed
```

## Run command

Use the same command used for run `UHAUL-POASN-FULL-DUMMY-20260720-225522`:

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

## Expected Rules sequence

```text
Exact Rule Name entered
  -> existing-name validation detected
  -> temporary unsaved structural name entered
  -> Document Type selected
  -> default Action Name and Type selected
  -> exact Mapping Identifier selected from owned async DDS list
  -> Execute Action(s) When selected
  -> Condition 1 filled and verified
  -> Conditions + creates exactly one new row
  -> Condition 2 filled and verified
  -> exact Rule Name restored
  -> no Save/Create/Submit action
```

Expected Conditions:

| Row | Condition Type | Operator | Value | Attribute |
|---|---|---|---|---|
| 1 | Attributes | Equals | uhaul | Receiver |
| 2 | Attributes | Contains | DELL | Sender |

Expected Action:

| Name | Type | Mapping Identifier Name (Version) |
|---|---|---|
| DELLCoXMLASNXX08C_U-HAUL_ROUTE | Route Document | DELLCoXMLASNXX08C_U-HAUL(1.0) |
