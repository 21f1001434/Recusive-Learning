# Rules KB Phase — Implementation Verdict

## Verdict
Implemented a dedicated SecureLink **Rules KB learner** based on the proven Document Type KB workflow.

The new phase is read-only and follows the same pattern:

1. Open SecureLink Rules page.
2. Capture listing UI, visible rows, buttons, and Network-tab API evidence.
3. Extract old Rule inventory from observed APIs.
4. Replay safe pagination/list APIs.
5. Enrich Rule IDs through read-only detail APIs.
6. Repeat safe UI row/action learning when list APIs hide numeric `ruleId`.
7. Capture deep Rule profiles from details API patterns.
8. Open `+ Add` safely.
9. Capture form controls, required fields, dropdowns, DOM hints, and add-form network evidence.
10. Fill disposable dummy values only.
11. Never click Save/Create/Submit/Delete.
12. Write final Rules KB JSON/CSV/Markdown/Knowledge Graph/upload ZIP outputs.

## New CLI command

```powershell
python -m hip_id_agent.cli discover-rules-kb `
  --config .\config.yaml `
  --customer RULES-KB `
  --input-json .\examples\uhaul_rules_dummy_input.json `
  --crawl-old-rules `
  --capture-deep-profiles `
  --max-api-pages 250
```

Do not pass `--max-detail-rows` or `--max-deep-profile-rows` if the goal is to learn all discovered Rules.

## Deep profile capture

The learner now attempts read-only details endpoints such as:

```text
/inaas-gateway/hipService-svc/api/rule/{ruleId}/details
/inaas-gateway/hipService-svc/api/rule/details?ruleId={ruleId}
/inaas-gateway/hipService-svc/api/rules/{ruleId}/details
```

It normalizes:

- `ruleId`
- `ruleName`
- `ruleVersion`
- status/description
- source Document Type ID/name/version
- target Document Type ID/name/version
- `ruleConditions[]`
- condition attribute/operator/value/expression/derived-from
- `ruleActions[]`
- action type
- mapping identifier/name/version
- linked flow/document type/map/transport usage evidence when exposed by the response wrapper

## Output files

The run writes a `rule_kb` folder containing:

- `rule_form_kb.json`
- `old_rules_inventory.json`
- `old_rules_inventory_with_ids.json`
- `old_rules_deep_profiles.json`
- `rule_deep_profile_report.json`
- `rule_deep_profile_enrichment_audit.json`
- `rule_id_completion_report.json`
- `rule_api_interactions.json`
- `rule_dropdowns.json`
- `rule_required_fields.json`
- `rule_form_controls.csv`
- `old_rules_inventory.csv`
- `RULE_KB_SUMMARY.md`
- upload summary ZIP

## Safety

This phase is explicitly read-only. It may open the Add form and fill dummy data to learn field behavior, but it does not save or create any Rule.

## Validation

Local tests passed:

```text
128 passed
```
