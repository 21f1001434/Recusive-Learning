# BizFlow KB Runtime + Dropdown Run Guide

Run full BizFlow KB capture:

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

Upload after completion:

```text
runs\BIZFLOW-KB-<timestamp>\UPLOAD_BIZFLOW_KB_SUMMARY.zip
```

Expected:
- old_bizflows: 249
- old_bizflows_with_numeric_id: 249
- deep_profiles_captured: 249
- runtime_profiles_captured: 249
- dropdown options enriched from live DOM and captured inventory/runtime evidence
