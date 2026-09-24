# Rule Repeatable Conditions Fix Verdict

## Problem fixed
The Rule form must match the golden Rules screenshot and input.json. UHAUL-POASN has two rule condition rows:

1. `Attributes | Equals | uhaul | Receiver`
2. `Attributes | Contains | DELL | Sender`

The previous runtime plan detected two rows, but the generic repeatable-row clicker selected the background Rules listing container instead of the small row-level `+ Add` beside `Conditions:`. As a result, the second Rule condition row was not created/fillable.

## Implemented fixes

- Added Rule-specific parsing of `objects.rule.conditions.rows`.
- Added Rule-specific parsing of `objects.rule.actions`.
- Added a dedicated condition-row clicker:
  - finds the visible `Conditions:` legend;
  - selects only the small nearby `+ Add`/plus icon;
  - blocks Save/Create/Submit/Delete/Deploy-style controls;
  - records a detailed repeatable-row audit.
- Added section-aware Rule filling:
  - distinguishes `Rule > Name` from `Actions > Name`;
  - fills repeated condition rows by occurrence order;
  - fills `Attribute Name/Unit` for both Receiver and Sender rows;
  - fills `Execute Action(s) When` from input.json;
  - fills Action Name, Action Type, and Mapping Identifier Name Version.
- Disabled the generic Rule repeatable clicker for Conditions so it cannot click the background listing container again. The generic plan is still written as audit-only evidence.

## Acceptance evidence

```text
pytest -q
197 passed
```

## Key files changed

- `hip_id_agent/rules_kb.py`
- `tests/test_rule_repeatable_conditions_patch.py`

## Run mode
Continue to use MCP-required mode:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --runs-dir "C:\hip_runs"
```
