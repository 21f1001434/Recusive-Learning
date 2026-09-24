# Rules KB Deep Profile Fix Verdict

## Verdict
Fixed. The Rules KB phase previously entered `rule_deep_profile_enrichment` after inventory and UI row-action learning, but crashed because `_capture_rule_deep_profiles` was not implemented.

## Evidence from user run
- Rules listing/API inventory discovered 283 rows.
- Detail ID enrichment completed with `ruleIds=0/283`.
- UI row-action learning completed 283/283 but still had `ruleIds=0/283`.
- The run entered `rule_deep_profile_enrichment` and failed with `NameError: name '_capture_rule_deep_profiles' is not defined`.

## Fixes applied
1. Added `_capture_rule_deep_profiles(...)`, matching the Document Type deep-profile pattern.
2. Reuses Rule detail payloads already captured from UI row-action network events.
3. Attempts read-only Rule details APIs by numeric `ruleId` when present.
4. Attempts read-only Rule details APIs by `ruleName`, `ruleVersion`, and environment when the numeric ID is hidden.
5. Adds broader Rule detail endpoint candidates:
   - `/api/rule/{id}/details`
   - `/api/rule/{id}/detail`
   - `/api/rules/{id}/details`
   - `/api/rule/details?ruleId=...`
   - `/api/rule/details?ruleName=...&environment=...&ruleVersion=...`
   - `/api/rule-details?...`
6. Improved numeric ID extraction to parse generic `id` inside Rule-shaped payloads.
7. Fixed `_event_dict` so dict-based network event evidence is parsed correctly.
8. Preserves expanded UI row text as partial evidence if no API profile is exposed.
9. Adds checkpoints:
   - `old_rules_deep_profiles.checkpoint.json`
   - `rule_deep_profile_enrichment_audit.checkpoint.json`
   - `rule_deep_profile_report.checkpoint.json`
10. Safe behavior unchanged: no Save/Create/Submit/Delete actions.

## Validation
`129 passed`

## Run command
```powershell
python -m hip_id_agent.cli discover-rules-kb `
  --config .\config.yaml `
  --customer RULES-KB `
  --input-json .\examples\uhaul_rules_dummy_input.json `
  --crawl-old-rules `
  --capture-deep-profiles `
  --max-api-pages 25000
```

Do not pass `--max-detail-rows` or `--max-deep-profile-rows` for a full run.
