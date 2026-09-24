# Transport Profile KB Deep-Learning Run Guide

## Purpose

Build a read-only SecureLink Transport Profile knowledge base using the same pattern proven for Document Types and Rules.

The learner captures:

- Transport Profile listing/API inventory
- Numeric `transportProfileId` where exposed
- UI row-action/detail evidence
- Deep profile payloads
- `transportProfileDetails`
- `interfaceDetails`
- `parameters` / connection parameters
- `documentTypeDetails`
- partner/account/system/document type references where exposed
- Add Transport Profile form controls
- required fields
- dropdown options
- dummy-fill evidence

Safety rule: the agent does **not** click Save/Create/Submit/Delete.

## Command

```powershell
python -m hip_id_agent.cli discover-transport-profile-kb `
  --config .\config.yaml `
  --customer TRANSPORT-PROFILE-KB `
  --input-json .\examples\uhaul_transport_profile_dummy_input.json `
  --crawl-old-transport-profiles `
  --capture-deep-profiles `
  --max-api-pages 25000
```

Do not pass `--max-detail-rows` or `--max-deep-profile-rows` for a full run.

## Expected final run evidence

The final run should show non-zero values for:

- `old_transport_profiles`
- `old_transport_profiles_with_numeric_id`
- `deep_profiles_captured`
- `form_controls`
- `required_fields`
- `dropdowns`

The final generated ZIP should include the `transport_profile_kb/` folder with inventory, API interaction, deep profile, form, dropdown, and knowledge graph files.

## URL

Target module:

```text
https://developer.dell.com/hybrid-integrations/securelink/transportprofiles
```

## Stuck-run fix note

If the page is visibly loaded but the terminal shows `rows=0` during `direct_summary_api_fallback` or `old_transport_profile_api_pagination`, this build will now fall back to the visible Transport Profiles grid and continue into UI row-action/deep-profile learning. Empty API fallback probes are capped internally, so `--max-api-pages 25000` will not cause a long stall at zero rows.

