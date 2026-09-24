# Document Type Usage row-scope reconciliation fix — 2026-07-16

## Run reviewed

`UHAUL-POASN-FULL-DUMMY-20260716-203753`

## Accepted root cause

The live DDS multi-select driver selected the requested Usage set in all five Source Document Type attribute rows:

- Flow Identifier Expression
- Logging
- Mapping
- Routing

The Playwright MCP action log contains five distinct DDS listbox IDs and four successful unique `aria-posinset` option clicks for each listbox.

The phase still failed because the post-fill state-graph collector/reconciler had two defects:

1. The row collector used the nearest nested `.dds__d-flex.dds__justify-content-start` wrapper as the repeatable-row identity. HIP places each field inside its own nested flex wrapper, so Attribute Name and Usage in the same business row could receive different row indexes.
2. The resolver allowed section + row score to outweigh semantic mismatch. For attribute rows 1–4 it therefore resolved an `attribute_usage` node to the same-row `attribute_name` input.
3. The first Usage row was captured correctly, but the collector included DDS presentation text `4 selected` as a fifth selected value, making exact-set comparison fail.

This was a verifier/reconciliation failure, not a failure of the MCP option clicks or of the portal multi-select itself.

## Runtime corrections

### Stable repeatable-row identity

For Document Identifier and Attributes To Configure fieldsets, every control now walks to the direct fieldset child that owns the complete business row. All controls in that row therefore share one row index.

### Strict semantic family matching

Repeated Document Type controls now resolve by semantic field family before row score:

- `attribute_name`
- `attribute_derived_from`
- `attribute_usage`
- `attribute_expression`
- document-identifier operation/derived-from/value

An Usage node can no longer resolve to Attribute Name merely because the row index matches. When an exact semantic family cannot be found, the resolver fails closed and recaptures the active form.

### Cosmetic count filtering

DDS count labels matching `^\\d+ selected$` are removed from selected values in:

- live Document Type state capture
- generic phase state capture
- saved-DOM artifact reconstruction
- exact-set comparison

The portal text `4 selected` remains visible evidence but is never interpreted as a selected option.

### Artifact row reconstruction

Saved post-fill DOM row indexing now uses the same direct-fieldset-child rule, keeping offline judges consistent with live execution.

## Safety retained

- No Save/Create/Submit/Delete/Deploy action is enabled.
- Exact four-option set verification remains mandatory.
- A missing Usage control remains blocking.
- No dynamic DDS ID is promoted as durable knowledge.
- Source and Target Document Types use the same fixed runtime.

## Verification

- Python compilation: passed
- Targeted stateful Document Type tests: 21 passed
- Complete automated suite: 298 passed
- Live authenticated rerun: not performed in this environment
