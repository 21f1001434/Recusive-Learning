# HIP Full Dummy Fill E2E Run Guide

This package adds a single end-to-end command that opens every phase link, clicks `+ Add`, fills safe dummy values, captures screenshots/DOM/network evidence, verifies required fields from DOM evidence, and optionally uses a vision model to review the filled screenshots.

## Covered links and phases

1. Data Map — https://developer.dell.com/hybrid-integrations/securelink/datamaps
2. Source Document Type — https://developer.dell.com/hybrid-integrations/securelink/doctypes
3. Target Document Type — https://developer.dell.com/hybrid-integrations/securelink/doctypes
4. Rule — https://developer.dell.com/hybrid-integrations/securelink/rules
5. Source Transport Profile — https://developer.dell.com/hybrid-integrations/securelink/transportprofiles
6. Target Transport Profile — https://developer.dell.com/hybrid-integrations/securelink/transportprofiles
7. BizFlow — https://developer.dell.com/hybrid-integrations/bizexchange/bizflows

## Safety policy

The runner fills values only. It does not click Save, Create, Submit, Delete, Remove, Deploy, Enable, Disable, Confirm, Publish or Update. It records any unsafe-click evidence into the phase verification report.

## Fast dummy-fill command

Use this first. It avoids re-crawling all old KB inventory where possible and focuses on Add form filling and screenshots.

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify
```

## Full KB-context command

Use this when the forms depend on old inventory/dropdown context and the fast run does not expose enough dropdown values. This is slower because each phase can re-learn old data before opening `+ Add`.

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --full-kb-context `
  --vision-verify
```

## Optional automatic vision verification

The command always writes `vision_verification_prompts.json`. To make it call a multimodal model automatically, set these environment variables before running:

```powershell
$env:HIP_VISION_ENDPOINT="https://<your-openai-compatible-endpoint>/chat/completions"
$env:HIP_VISION_TOKEN="<token>"
$env:HIP_VISION_MODEL="<vision-model-name>"
```

If these are not set, the runner still performs deterministic DOM verification and keeps the prompts/screenshots ready for manual or later vision review.

## Output

The aggregate run creates:

- `FULL_DUMMY_FILL_E2E_REPORT.html`
- `full_dummy_fill_summary.json`
- `phase_verification_report.csv`
- `vision_verification_prompts.json`
- `vision_verification_results.json`
- `UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip`

Each phase also keeps its own subfolder with DOM snapshots, form controls, required fields, dropdowns, dummy fill plan and screenshots.
