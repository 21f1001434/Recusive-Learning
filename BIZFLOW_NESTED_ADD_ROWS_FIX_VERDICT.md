# BizFlow Nested +Add Rows Fix Verdict

## Evidence Used
Latest uploaded run: `UHAUL-POASN-FULL-DUMMY-20260709-202115`.

The run shows the major phases are now reaching the BizFlow wizard and BizFlow shallow verification passes with 55 field steps, but the user's golden-image truth requires exact nested row creation:

1. Configure Source: click `+ Add` beside Attribute / Flow Identifier and fill all conditions from input.json.
2. Configure Target(s): click `+ Add` beside Process Step and fill process step rows from input.json.
3. Configure Routing: click top-level `+ Add`, then inside the routing rule drawer click `+ Add` beside Conditions and `+ Add` beside Actions, then fill rows from input.json.

The previous generic repeatable-row finder could mis-rank app header / table container / Add Target controls as row-level adds. This patch replaces generic BizFlow repeatable clicking with a dedicated BizFlow nested-row workflow.

## Implemented Fixes

### 1. Dedicated BizFlow nested row plan
Added `build_bizflow_nested_row_plan(input_data)` in `hip_id_agent/bizflow_kb.py`.

It derives exact row actions from `input.json`:

- `objects.biz_flow.flow_identifiers.conditions`
- `objects.biz_flow.process_steps`
- `objects.biz_flow.configure_routing.conditions.rows`
- `objects.biz_flow.configure_routing.actions`

### 2. Strict row-level `+ Add` finder
Added `_click_bizflow_section_add(...)`.

It scopes to the active Create Biz Flow form and rejects:

- app header / masthead
- side nav
- generic grid/table containers
- Save/Create/Submit/Delete/Deploy/Update actions
- large shell elements

It prefers small real Add/plus controls near section labels such as Attribute, Process Step, Conditions, and Actions.

### 3. Replaced generic BizFlow repeatable planner
`capture_and_fill_bizflow_multitab_form(...)` now uses `_apply_bizflow_nested_row_adds(...)` instead of the generic `apply_repeatable_row_adds(...)` for BizFlow tabs.

### 4. Input-driven row filling
Added row-specific fillers:

- `_fill_bizflow_source_attribute_rows(...)`
- `_fill_bizflow_process_step_rows(...)`
- `_fill_bizflow_routing_action_rows(...)`

These fill values from the uploaded/input JSON, including:

- Source Attribute conditions:
  - Receiver / Equals / uhaul
  - Sender / Contains / DELL
- Target Process Steps:
  - Mapping Transformer / Mapping-1 / Mapping
  - Enricher / Enricher-1 / Enrich
- Routing Action:
  - FLOWACTION_U-HAUL_PC_856_ANS_XMLASN_OB_ROUTE
  - Route Document
  - SFTP_U-HAUL_ASN_PC_TGT_OB

## Validation

```text
pytest -q
199 passed, 2 warnings
```

## Changed Files

```text
hip_id_agent/bizflow_kb.py
tests/test_bizflow_kb.py
BIZFLOW_NESTED_ADD_ROWS_FIX_VERDICT.md
```
