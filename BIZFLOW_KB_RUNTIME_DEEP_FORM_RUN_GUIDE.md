# BizFlow Runtime/Deployment Deep Profile Run Guide

Run this from the project root after extracting the updated package:

```powershell
python -m hip_id_agent.cli discover-bizflow-kb `
  --config .\config.yaml `
  --customer BIZFLOW-KB `
  --input-json .\examples\uhaul_bizflow_dummy_input.json `
  --crawl-old-bizflows `
  --capture-deep-profiles `
  --capture-runtime-details `
  --max-api-pages 25000 `
  --fill-dummy
```

Upload after run:

```text
runs\BIZFLOW-KB-<timestamp>\UPLOAD_BIZFLOW_KB_SUMMARY.zip
```

If you want to validate form/dropdowns only, run:

```powershell
python -m hip_id_agent.cli discover-bizflow-kb `
  --config .\config.yaml `
  --customer BIZFLOW-FORM-KB `
  --input-json .\examples\uhaul_bizflow_dummy_input.json `
  --form-only `
  --fill-dummy
```

The final full run should generate runtime files inside the ZIP:

- `bizflow_runtime_deployment_profiles.json`
- `bizflow_runtime_deployment_report.json`
