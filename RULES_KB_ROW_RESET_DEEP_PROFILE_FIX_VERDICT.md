# Rules KB Deep Learning Fix Verdict — Row Reset Patch

## Verdict

The latest Rules run completed, but it is still not complete enough to be considered the final Rules KB. It captured real deep profiles for 149 of 282 unique Rules and captured the Add Rule form, but 133 Rules remained without deep profiles because the UI row-action loop became unstable while iterating through the Rules grid.

## Evidence from latest run

- Status: completed
- Old Rules evidence rows: 582
- Unique Rules attempted for deep profiles: 282
- Rules with numeric ID: 149
- Real deep profiles captured: 149/282
- Add Rule form controls captured: 12
- Required fields captured: 8
- Dropdowns captured: 2

## Root Cause

The Rules grid row-action learner still had two instability cases:

1. **Search/listing state was not reset between failed rows.**
   After opening or expanding a row, the next iteration could fail to find the listing search box or row, resulting in `search_failed` and no Rule ID.

2. **Already-expanded rows were mishandled.**
   When a row button displayed `Collapse the row`, the learner treated it as an unsafe/non-useful action or would risk clicking it closed. That caused some rows with visible details to be missed.

## Fix Implemented

- Added `_reset_rules_listing_for_row_learning(...)`.
- Retries failed row-action learning once after resetting the Rules listing.
- Handles `search_failed`, `row_not_found`, and `no_safe_row_action` with a clean retry.
- Treats `Collapse the row` as an already-expanded read-only state and captures the visible expanded evidence without closing it.
- Adds periodic cleanup every 10 rows to prevent accumulated row/drawer/search state from contaminating the next Rule.

## Safety

No mutation actions were added. The patch only searches, expands/views rows, reads network evidence, and captures Add-form structure. Save/Create/Submit/Delete remain blocked.

## Test Result

```text
134 passed
```

## Required Next Run Command

```powershell
python -m hip_id_agent.cli discover-rules-kb `
  --config .\config.yaml `
  --customer RULES-KB `
  --input-json .\examples\uhaul_rules_dummy_input.json `
  --crawl-old-rules `
  --capture-deep-profiles `
  --max-api-pages 25000
```

The next run should improve `ruleIds` and `deep_profiles_captured` beyond the latest 149/282 result. The final Rules HTML should be generated only after the run captures deep profiles for all or nearly all Rules, or after any remaining portal-hidden rows are explicitly documented.
