# Rule Conditions + TP Screenshot + BizFlow Template Launch Runtime Fix Verdict

## Evidence used
Latest run bundle: `UHAUL-POASN-FULL-DUMMY-20260709-172450.zip`.

## Runtime findings
- Data Map: pass.
- Source Document Type: pass.
- Target Document Type: pass.
- Rule: pass at gate level, but warning showed the repeatable condition `+ Add` clicker selected a background Rules table row expand icon instead of the small `Conditions:` legend +Add button.
- Source/Target Transport Profile: fields were filled, but the golden-truth screenshot was not attached, so strict replication failed with `No screenshot found for filled form phase`.
- BizFlow: still stopped at the template picker. The actual launch path is the template card overflow menu item `Create Biz Flow`; the Inbound/Outbound tags are not reliable launch buttons.

## Implemented fixes

### 1. Rule repeatable Conditions rows
Changed `hip_id_agent/rules_kb.py`:
- `_find_rule_condition_add_candidate()` now scopes only to the visible `Create Rule` drawer/form.
- It directly resolves the `Conditions:` fieldset legend and the embedded `Create Condition` / `add-cir` button.
- It no longer scans the background DDS Rules listing grid.
- `_apply_rule_condition_row_adds()` now has normal click, force click, and DOM click fallback for the exact Conditions legend button.
- Label matching was tightened so `Document Type Name (Version)` no longer maps to the generic `Name` field.

Expected Rule behavior:
- Click `+ Add` beside `Conditions:` once when `input.json` has 2 conditions.
- Fill row 1: `Attributes | Equals | uhaul | Receiver`.
- Fill row 2: `Attributes | Contains | DELL | Sender`.

### 2. Transport Profile golden screenshot evidence
Changed `hip_id_agent/transport_profile_kb.py`:
- `_save_transport_profile_golden_truth_screenshot()` now writes screenshots through multiple independent fallbacks:
  1. direct Playwright `page.screenshot`,
  2. BrowserSession screenshot,
  3. active form root screenshot,
  4. body screenshot fallback.
- It verifies the PNG exists and is non-empty before returning success.

Expected TP behavior:
- Source/Target TP should no longer fail only because screenshot evidence is missing.

### 3. BizFlow template launch
Changed `hip_id_agent/bizflow_kb.py`:
- `_click_bizflow_template_link_after_add()` now follows the actual observed DOM from the latest run:
  1. find the exact `B2B-Flow-PubSub-Template` card,
  2. click the card overflow/action-menu button,
  3. click the visible `Create Biz Flow` menu item,
  4. verify the actual Create Biz Flow wizard tabs are visible.
- It does not treat Inbound/Outbound tags as launch controls.
- It does not click the page container, side nav, listing grid, cookie UI, or final Save/Create buttons.

## Validation
`pytest -q` result:

```text
197 passed
```

## Files changed
- `hip_id_agent/rules_kb.py`
- `hip_id_agent/transport_profile_kb.py`
- `hip_id_agent/bizflow_kb.py`
- `RULE_CONDITIONS_TP_BIZFLOW_RUNTIME_FIX_VERDICT.md`
