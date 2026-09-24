# SecureLink Document Type KB Discovery

This build adds the Document Type equivalent of the completed Data Map KB learner.

## Command

```powershell
python -m hip_id_agent.cli discover-doctype-kb `
  --config .\config.yaml `
  --customer DOCTYPE-KB `
  --input-json .\examples\uhaul_doctype_dummy_input.json `
  --known-document-type-id 10483 `
  --crawl-old-doctypes `
  --max-api-pages 250
```

## What it does

1. Opens `https://developer.dell.com/hybrid-integrations/securelink/doctypes`.
2. Waits through Dell SSO and avoids the blank-page double-navigation issue.
3. Learns Document Type listing APIs and direct summary fallback APIs.
4. Extracts old Document Type IDs and required details from API payloads.
5. Replays observed pagination where supported.
6. Attempts bounded read-only detail endpoint enrichment.
7. Repeats the real UI row/action/expand flow for all discovered Document Types by default to learn hidden numeric IDs, like the Partner/System and Data Map learners.
8. Clicks `+ Add`, captures Add form controls, required fields, dropdowns, DOM events.
9. Fills dummy values only and never clicks Save/Create/Submit/Delete.
10. Writes uploadable summary ZIP and a Document Type API Flow Knowledge Graph.

Note: omit `--max-detail-rows` for a full run. Use `--max-detail-rows` only when you intentionally want a small smoke test.

## Upload after run

Upload only:

```text
runs\<RUN_ID>\UPLOAD_DOCTYPE_KB_SUMMARY.zip
```

## Key output files

```text
runs\<RUN_ID>\doctype_kb\old_doctypes_inventory_with_ids.json
runs\<RUN_ID>\doctype_kb\old_doctypes_inventory_with_ids.csv
runs\<RUN_ID>\doctype_kb\doctype_id_completion_report.json
runs\<RUN_ID>\doctype_kb\doctype_detail_enrichment_audit.json
runs\<RUN_ID>\doctype_kb\doctype_ui_row_action_enrichment_audit.json
runs\<RUN_ID>\doctype_kb\doctype_form_kb.json
runs\<RUN_ID>\doctype_kb\doctype_required_fields.json
runs\<RUN_ID>\doctype_kb\doctype_dropdowns.json
runs\<RUN_ID>\doctype_kb\doctype_api_flow_knowledge_graph.html
runs\<RUN_ID>\UPLOAD_DOCTYPE_KB_SUMMARY.zip
```

## Full Deep Profile Run

Use this for the final Document Type KB before moving to the next phase:

```powershell
python -m hip_id_agent.cli discover-doctype-kb `
  --config .\config.yaml `
  --customer DOCTYPE-KB `
  --input-json .\examples\uhaul_doctype_dummy_input.json `
  --known-document-type-id 10483 `
  --crawl-old-doctypes `
  --capture-deep-profiles `
  --max-api-pages 250
```

Expected extra outputs:

- `old_doctypes_deep_profiles.json`
- `doctype_deep_profile_enrichment_audit.json`
- `doctype_deep_profile_report.json`

Important: omit `--max-detail-rows` and `--max-deep-profile-rows` to run all discovered Document Types.
