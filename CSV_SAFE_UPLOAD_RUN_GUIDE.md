# Run Guide

Run from the repository root:

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
  --upload-assets-dir ".\uploads"
```

The output will be saved under the `runs` path configured in `config.yaml`.

After the run completes, upload:

```text
runs\UHAUL-POASN-FULL-DUMMY-<timestamp>\UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip
```
