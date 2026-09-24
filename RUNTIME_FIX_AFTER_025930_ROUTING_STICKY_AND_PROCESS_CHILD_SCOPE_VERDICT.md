# Runtime Fix After 025930 — Routing Sticky Lock + Process Child Scope

## Verdict
The 025930 evidence is still not fully correct.

Passing:
- Data Map
- Source Document Type
- Target Document Type

False failing:
- Source Transport Profile screenshot exists, but aggregate verifier reported screenshots=0.
- Target Transport Profile screenshot exists, but aggregate verifier reported screenshots=0.

Still unstable:
- Rule has warnings only.
- BizFlow is still pass_with_warnings.

## Evidence Root Cause
The agent is extracting many controls/options, but BizFlow repeatable-row execution still has three runtime issues:

1. Sticky restore wrote a stale value into the wrong selector.
   - Example from 025930: a selector used for Condition Type was later restored with Attribute Name value `Receiver`.
   - This creates the visible “filled then overwritten/unfilled” behavior.

2. Configure Target -> Process Step child fields are rendered outside the literal accordion item.
   - `Step Type` and `Step Name` filled.
   - `Action`, `Target Document Type`, and `Rule` were not found because the scanner stayed too tightly scoped to the accordion item.

3. Configure Routing drawer row fields need geometry-based mapping.
   - Labels are not repeated on every row.
   - Conditions and Actions must be mapped by active drawer section, row, and column position, not by global label matching.

## Implemented Fix
- Disabled global sticky restore inside deterministic BizFlow repeatable rows.
- Added BizFlow lock clearing before/after nested row fills.
- Added active routing drawer geometry reader:
  - Conditions rows = condition type, operator, value, attribute name/unit
  - Actions rows = name, type, target
- Added loading-overlay wait after Action Type = Route Document before searching Target.
- Added Process Step Mapping Configuration fallback outside the accordion item.
- Strengthened TP screenshot recovery using deterministic local PNG paths.

## Offline Verification Against 025930 Evidence
- Source Transport Profile: pass, screenshots=1
- Target Transport Profile: pass, screenshots=1
- BizFlow: old evidence remains pass_with_warnings because the failed attempts were already recorded in that run; the next run should not create the same stale sticky/routing failures.

## Validation
`pytest -q` => 204 passed in 8.31s
