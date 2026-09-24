# Rules KB Run Guide

## Purpose

Use this phase after Document Type KB completion to learn the SecureLink Rules module with the same evidence depth:

- full old Rule inventory
- numeric Rule IDs
- row-action UI evidence
- Rule API/list/detail evidence
- Add Rule form controls
- dropdowns and required fields
- full deep Rule profiles including conditions and actions

## Command

```powershell
python -m hip_id_agent.cli discover-rules-kb `
  --config .\config.yaml `
  --customer RULES-KB `
  --input-json .\examples\uhaul_rules_dummy_input.json `
  --crawl-old-rules `
  --capture-deep-profiles `
  --max-api-pages 250
```

## Full-run notes

- Keep `--crawl-old-rules` enabled.
- Keep `--capture-deep-profiles` enabled.
- Do not pass `--max-detail-rows` unless you want a limited test run.
- Do not pass `--max-deep-profile-rows` unless you want a limited test run.
- The learner never clicks Save/Create/Submit/Delete.

## Expected progress phases

You should see phases like:

```text
rule_kb_login
rule_kb_open_rules
rule_page_ready
old_rule_api_pagination
rule_detail_id_enrichment
rule_ui_row_action_id_learning
rule_deep_profile_enrichment
find_add_button
click_add
capture_add_form
fill_dummy
summarize_and_write_outputs
completed
```

## Main output folder

```text
runs/RULES-KB-YYYYMMDD-HHMMSS/rule_kb/
```

## Files to upload for review

Upload the final summary ZIP printed at the end of the run, or upload these files:

- `rule_form_kb.json`
- `old_rules_inventory_with_ids.json`
- `old_rules_deep_profiles.json`
- `rule_deep_profile_report.json`
- `rule_id_completion_report.json`
- `RULE_KB_SUMMARY.md`
