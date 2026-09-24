# Strict Replication Run Guide

Run from the project root after extracting the ZIP:

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

After completion upload:

```text
runs\UHAUL-POASN-FULL-DUMMY-<timestamp>\UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip
```

Review these files first:

```text
full_dummy_fill_summary.json
phase_verification_report.csv
<phase>\strict_replication_gate.json
fast_replay_blueprints\*.json
```

A phase is not valid unless `status` is `pass` and `strict_replication_gate.fatal` is empty.
