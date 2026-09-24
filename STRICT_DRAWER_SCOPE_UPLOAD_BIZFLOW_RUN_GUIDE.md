# Run Guide

Use the same command:

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

Trust the run only when every phase has:

```json
"fatal": []
```

in:

```text
runs\UHAUL-POASN-FULL-DUMMY-<timestamp>\<phase>\strict_replication_gate.json
```
