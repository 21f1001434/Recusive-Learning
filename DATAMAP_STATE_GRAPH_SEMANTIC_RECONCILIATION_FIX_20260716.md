# HIP Data Map state-graph semantic reconciliation fix

## Run analyzed

`UHAUL-POASN-FULL-DUMMY-20260716-183923`

## What actually happened

The specialized Data Map filler successfully committed the requested values before the new state-graph verifier ran:

- Map Identifier: `DELLCoXMLASNXX08C_U-HAUL`
- Map Identifier Version: portal rendered `1.0` for input `1`
- Status switch: enabled
- Map Name: `DELLCoXMLASNXX08C`
- Map Class: `Transform_DELLCoXMLASNXX08C`
- Contivo Version: `6.7`
- Map Data: `Transform_DELLCoXMLASNXX08C.jar`

The verifier then incorrectly reported four controls as missing. The saved `final_controls` evidence proves those controls existed and contained exact values.

## Root causes

1. The generic stateful collector generated durable `semantic_key` values such as `map_identifier`, `map_name`, `map_class`, and `contivo_version`, but `resolve_stateful_control()` did not score `semantic_key` against the graph node `field_key`.
2. Graph nodes were scoped to the overall `Create Map` surface, while live controls were scoped to nested fieldsets such as `Map Reference :` and `Mapping Details :`. The resolver applied a section-mismatch penalty even though both were inside the validated Create Map drawer.
3. The Data Map status is an unlabeled DDS switch. Two switches were present, and the graph lacked subsection/role evidence to distinguish the status switch from Cross Reference Table Details.
4. `BrowserSession.wait_ready()` contained an accidental DOM-transition call using undefined variables (`action_type`, `selector`, `dom_cursor`). This generated repeated non-fatal action errors.
5. There was no final exact-state reconciliation pass before failing the phase.

## Implementation changes

- `semantic_key == field_key` is now the strongest generic resolver signal.
- State graph locators now support live role and section aliases.
- Data Map graph nodes include live names, roles, and nested fieldset aliases.
- Generic Create Map/Create Transport Profile nodes no longer receive a hard section penalty for nested fieldsets.
- Precise subsection aliases score higher than generic form-family scope, preventing the wrong switch from being selected.
- DDS switch values are verified as Enabled/Disabled through `checked` state.
- Version equivalence continues to accept `1` and portal-rendered `1.0` as exact numeric equivalents.
- A final exact live-control reconciliation pass runs before a state graph is marked failed.
- `wait_ready()` no longer records a semantic DOM transition and no longer references undefined variables. Actual click/fill/select actions remain event-captured.

## Safety

The fix does not weaken fail-closed behavior. Reconciliation succeeds only when:

1. a control resolves through live semantic identity;
2. the actual committed value exactly equals the input value;
3. row scope and section scope remain unambiguous;
4. uploads are accepted by the phase-specific upload contract.

No Save/Create/Submit/Delete/Deploy action is enabled.

## Verification

- Uploaded run final-control replay: all seven Data Map nodes resolve and verify exactly.
- Targeted regression tests: 5 passed.
- Existing navigation timeout regression: passed.
- Complete test suite executed in five isolated batches: 288 passed.
- Python compilation: passed.

## Honest verdict

The supplied run terminated because of a false-negative verifier, not because Data Map was unfilled. The patch is ready for another authenticated no-save run. Later phases still require live proof.
