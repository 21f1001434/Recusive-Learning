# Document Type KB Full-Run Patch Verdict

## Status

Fixed and re-verified.

This patch keeps the existing Data Map learner untouched. The change is only for the SecureLink **Document Type** learner so it can run the Document Type page and collect the full discovered Document Type information set.

## What was corrected

1. **Full Document Type coverage by default**
   - Previous behavior: when `--max-detail-rows` was omitted, the Document Type learner silently used `max_api_pages` as the row cap.
   - New behavior: when `--max-detail-rows` is omitted, the learner enriches / UI-learns **all discovered Document Type rows**.
   - `max_api_pages` now controls only API pagination replay, not the number of Document Types to process.

2. **Document Type naming cleanup**
   - Internal helper/report keys now use `doctype` / `document_type` language for Document Type detail matching and candidate URL limits.
   - Data Map code remains as-is and is not treated as wrong.

3. **Higher default pagination for Document Type inventory**
   - `discover-doctype-kb` now defaults `--max-api-pages` to `250`, so the run is suitable for large Document Type inventories without needing extra CLI flags.

4. **Full information output remains enabled**
   The run still writes:
   - `old_doctypes_inventory.json/csv`
   - `old_doctypes_inventory_with_ids.json/csv`
   - `doctype_api_interactions.json`
   - `doctype_detail_enrichment_audit.json`
   - `doctype_ui_row_action_enrichment_audit.json`
   - `doctype_id_completion_report.json`
   - `doctype_form_kb.json`
   - `doctype_required_fields.json`
   - `doctype_dropdowns.json`
   - `doctype_api_flow_knowledge_graph.*`
   - `UPLOAD_DOCTYPE_KB_SUMMARY.zip`

## Correct command

Use this for the full Document Type KB run:

```powershell
python -m hip_id_agent.cli discover-doctype-kb `
  --config .\config.yaml `
  --customer DOCTYPE-KB `
  --input-json .\examples\uhaul_doctype_dummy_input.json `
  --known-document-type-id 10483 `
  --crawl-old-doctypes `
  --max-api-pages 250
```

Do **not** pass `--max-detail-rows` unless you intentionally want to limit the run for a quick test.

For a quick smoke test only:

```powershell
python -m hip_id_agent.cli discover-doctype-kb `
  --config .\config.yaml `
  --customer DOCTYPE-KB-SMOKE `
  --input-json .\examples\uhaul_doctype_dummy_input.json `
  --known-document-type-id 10483 `
  --crawl-old-doctypes `
  --max-api-pages 25 `
  --max-detail-rows 25
```

## Verification

Local regression suite:

```text
118 passed
```

## Final verdict

Accepted for the next live Document Type KB run. The learner should now run against Document Types, preserve Data Map functionality separately, and collect full discovered Document Type inventory/form/API/ID evidence unless the user explicitly caps `--max-detail-rows`.
