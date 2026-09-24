# Golden Replication Ready

This package is updated to replicate the human-approved U-HAUL POASN filled form screenshots.

## What changed

- Bundled the approved screenshots under `golden_screenshots/UHAUL-POASN/`.
- Added `--golden-screenshot-dir` to `run-full-dummy-fill`.
- Copies golden screenshots into each run under `golden_reference_screenshots/`.
- Maps golden screenshots to phases:
  - Data Map
  - Source Document Type
  - Target Document Type
  - Rule
  - Source Transport Profile
  - Target Transport Profile
  - BizFlow Flow Details
  - BizFlow Configure Source
  - BizFlow Configure Target
  - BizFlow Configure Routing
  - BizFlow Deployed/reference view
- Adds golden references into every per-phase fast replay blueprint.
- Generates vision prompts that compare the actual after-fill screenshot against the golden screenshot.
- Keeps MCP required mode and no-save safety.

## Run

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --runs-dir "C:\hip_runs"
```

## Outputs

- `golden_reference_screenshots/golden_reference_manifest.json`
- `flash_fill_replay_manifest.json`
- `fast_replay_blueprints/*_fast_fill_blueprint.json`
- `vision_verification_prompts.json`
- `FULL_DUMMY_FILL_E2E_REPORT.html`
- `UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip`

## Safety

This still does not click Save/Create/Submit/Delete/Deploy. It replicates the filled state and saves the replay blueprint only.
