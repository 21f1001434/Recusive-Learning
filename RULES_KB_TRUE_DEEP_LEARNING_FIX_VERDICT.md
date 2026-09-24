# Rules KB True Deep Learning Fix Verdict

## Verdict
The uploaded run `RULES-KB-20260707-005925` is **not acceptable for final Rules KB deep learning**. It completed the loop but returned `partial_success` because:

- `deep_profiles_captured` was `0`.
- 283/283 rows were marked as partial from the `/api/rule/summary` listing payload.
- `ruleIds` remained `0/283` after detail and UI row-action phases.
- Add Rule form capture produced `form_controls=0`, `required_fields=0`, and `dropdowns=0` because an `app-generic-drawer` overlay intercepted the Add button.

## Root Causes Fixed

1. **Undefined JavaScript global**
   - The Rules row/action JS accidentally referenced `rule.querySelectorAll(...)` and `rule.body` instead of `document.querySelectorAll(...)` and `document.body`.
   - This caused UI row-action detail clicks to fail and prevented proper DOM/form capture.

2. **False deep-profile reuse**
   - The deep profile phase reused `/api/rule/summary` rows as if they were real deep profiles.
   - The new patch blocks `/api/rule/summary` from satisfying deep-profile capture.
   - Only detail-quality payloads with rule configuration evidence can be marked captured or reused.

3. **Stale drawer overlay before + Add**
   - A row-action drawer remained open and intercepted the Add button click.
   - The new patch closes stale Rules drawers/menus before the Add form capture phase.

## Code Changes

- Added `_is_rule_summary_endpoint(...)`.
- Added `_is_full_rule_deep_profile(...)`.
- Added `_should_reuse_network_rule_profile(...)`.
- Added `_close_rule_transient_surfaces(...)`.
- Replaced accidental JS `rule.*` references with `document.*`.
- Changed deep profile logic so partial/listing-shaped summary rows do not stop detail endpoint attempts.
- Added regression tests to prevent this issue from returning.

## Validation

```text
132 passed
```

## Correct Full Run Command

```powershell
python -m hip_id_agent.cli discover-rules-kb `
  --config .\config.yaml `
  --customer RULES-KB `
  --input-json .\examples\uhaul_rules_dummy_input.json `
  --crawl-old-rules `
  --capture-deep-profiles `
  --max-api-pages 25000
```

Do not pass `--max-detail-rows` or `--max-deep-profile-rows` for the full run.

## Expected Improvement

After this patch, the Rules learner will no longer report summary/listing rows as deep learning. The final summary must be judged by:

- `deep_profiles_captured` > 0
- `form_controls` > 0
- `required_fields` > 0
- `dropdowns` > 0
- no `app-generic-drawer` Add-click interception warning

If the portal truly does not expose deep Rule details, the report will now honestly show missing profiles instead of falsely counting 283 summary rows as deep profiles.
