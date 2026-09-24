# Rerun guide

Use the complete patched package and retain the existing Portal Brain directory.

## 1. Back up memory

```powershell
Copy-Item ".\data\hip_memory\portal_brain" ".\data\hip_memory\portal_brain_backup_20260716" -Recurse -Force
```

## 2. Extract the new package into a new directory

Copy your existing `.env`, `config.yaml`, `uploads`, `golden_screenshots`, and `data\hip_memory\portal_brain` into the extracted directory. Do not overwrite the patched source files.

## 3. Validate locally

```powershell
python -m compileall .\hip_id_agent
python -m pytest -q
python -m hip_id_agent.cli vision-preflight --config .\config.yaml --vision-model "gemma-3-27b-it"
```

## 4. Run

```powershell
$RUNS_DIR = Join-Path (Get-Location) "runs"

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

## Expected Data Map evidence

`data_map\datamap_kb\datamap_target_branch_execution.json` should contain:

```json
{
  "pass": true,
  "failed_attempts": []
}
```

The attempts should use reasons such as `specialized filler committed exact value` or `final live-control exact reconciliation`, not `required semantic control not found`.

The run should then continue to Source Document Type.
