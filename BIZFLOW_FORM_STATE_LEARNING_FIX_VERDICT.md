# BizFlow Form-State Learning Fix Verdict

## Latest evidence reviewed
Run: `UHAUL-POASN-FULL-DUMMY-20260709-214727`

Observed status from the run bundle:

- Data Map: pass
- Source Document Type: pass
- Target Document Type: pass
- Rule: pass
- Source Transport Profile: failed only because aggregate report still did not attach the existing PNG evidence
- Target Transport Profile: failed only because aggregate report still did not attach the existing PNG evidence
- BizFlow: pass_with_warnings

The BizFlow warnings were not random. The run evidence showed that the agent had a large DOM/option inventory, but the nested row-add selector still selected DDS dropdown chevrons as if they were row-level `+ Add` buttons. That means it opened dropdowns instead of creating Process Step / Condition / Action rows. This is why it could “know” all fields/options but still fail exact form fill.

## Root cause

Selector inventory alone is insufficient for HIP DDS/Angular forms. The form is stateful:

1. Some controls appear only after another value is selected.
2. Some controls appear only after a row-level `+ Add` button creates a row.
3. DDS dropdown chevrons use SVG/path markup and were being scored as plus/add controls.
4. Later dependency refreshes can clear or rerender values.

## Fix implemented

### 1. Reject DDS dropdown chevrons as `+ Add`

`_click_bizflow_section_add()` now rejects controls inside:

- `.dds__dropdown`
- `dds-dropdown`
- `.dds__search`
- `.dds__input-container`
- `.dds__dropdown__input-wrapper`
- `.dds__dropdown__chevron`

A row-creation Add must now be a real Add button or standalone plus outside picker/input wrappers.

### 2. Added form-state learning snapshots

Added `_capture_bizflow_form_state()` and `_bizflow_state_delta()`.

The agent now records before/after state for:

- tab open
- row-level `+ Add`
- every targeted field fill
- routing top-level Add

The audit now explains which controls were added, removed, or changed after each action. This turns the run evidence into a real dependency graph instead of a flat list of controls.

### 3. Fill attempts now carry `state_delta`

Each input-driven row fill attempt now includes before/after changes, so future failures show whether:

- the control was never created,
- a value was accepted,
- a dependent child appeared,
- a later action cleared a previous value.

### 4. Sticky restore remains active

The earlier sticky value lock remains in place, but selector-conflict protection prevents restoring the wrong value into a reused selector.

## Validation

```text
pytest -q
202 passed, 2 warnings in 10.15s
```

## Important note

This patch changes the agent from “I saw a dropdown/control” to “I know what action created which fields and whether filling changed the form state.” That is the missing piece for future profiles where fields appear only after other values are selected.
