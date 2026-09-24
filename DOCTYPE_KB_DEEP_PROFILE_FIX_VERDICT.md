# Document Type KB Deep Profile Capture Fix Verdict

## Verdict

Implemented. The Document Type KB learner now has a third enrichment phase that captures full per-Document-Type configuration profiles after inventory and ID learning.

## What changed

The previous build completed inventory + ID learning, but it only used `/api/document-type/{id}/details` responses as ID evidence. It did not normalize the full configuration body into each Document Type record.

This patch adds full deep profile capture:

- Reuses already captured row-action network payloads when they contain detail bodies.
- Calls the observed details endpoint directly for every learned Document Type ID:
  - `/inaas-gateway/hipService-svc/api/document-type/{documentTypeId}/details`
- Parses the observed detail wrapper:
  - `documentTypeDetail`
  - `flowDetail`
  - `ruleDetail`
  - `tpDetail`
- Normalizes deep fields into each Document Type record:
  - `documentIdentifier`
  - identifier operation
  - identifier derived-from rows
  - `rootElement`
  - `documentVersion`
  - `schemaFile`
  - attributes list
  - attribute derived-from
  - attribute usage
  - attribute expression / XPath
  - related flow/rule/transport-profile usage evidence
- Writes checkpoint files during the long run so partial evidence is preserved.
- Adds output files:
  - `old_doctypes_deep_profiles.json`
  - `doctype_deep_profile_enrichment_audit.json`
  - `doctype_deep_profile_report.json`
- Keeps safety rule unchanged: no Save/Create/Submit/Delete is clicked.

## New CLI options

```powershell
--capture-deep-profiles / --no-capture-deep-profiles
--max-deep-profile-rows <N>
```

By default, deep profile capture is enabled and runs for all discovered Document Types.

## Validation

```text
123 passed
```

## Recommended full run command

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

Do not pass `--max-detail-rows` or `--max-deep-profile-rows` if you want all discovered Document Types.
