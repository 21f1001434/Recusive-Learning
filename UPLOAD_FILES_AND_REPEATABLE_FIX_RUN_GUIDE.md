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

No `--runs-dir` is required because `config.yaml` is already set to:

```text
C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs
```

After the run, upload:

```text
runs\UHAUL-POASN-FULL-DUMMY-<timestamp>\UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip
```
