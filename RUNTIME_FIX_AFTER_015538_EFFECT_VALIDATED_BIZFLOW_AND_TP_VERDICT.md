# Runtime Fix After 015538 — Effect-Validated BizFlow + TP Screenshot Recovery

## Verdict
The 015538 evidence shows the agent had already extracted enough labels/options/DOM metadata, but it still filled incorrectly because it was using a flat selector inventory without strict effect validation for nested DDS components.

## Evidence from 015538
- BizFlow captured 98 field steps and 96 dropdowns, but still ended `pass_with_warnings`.
- Failed attempts remained for:
  - Configure Target -> Process Step row 0 child fields: Action, Target Document Type, Rule.
  - Configure Routing -> second condition row: Operator, Value, Attribute Name/Unit.
- Final screenshot showed duplicate Actions row and wrong Action Type (`Attributes`) in the extra row.
- Source/Target Transport Profile screenshot PNGs existed in phase folders but aggregate verification still reported screenshot count 0.

## Root Cause
1. Generic fill ran before deterministic row fill and touched repeatable BizFlow rows.
2. Routing Actions +Add was clicked even when the drawer already had an empty/default action row, creating duplicate action rows.
3. Process Step child controls may render outside the literal `dds-accordion-item` but still inside `app-process-step`, so row scanner missed Mapping Configuration fields.
4. Routing/Actions section anchoring was too broad and could match text like `Execute Action(s) When` instead of the actual `Actions:` section.
5. Windows absolute screenshot paths needed robust PureWindowsPath filename recovery.

## Fixes Implemented
- Added effect-based Add logic:
  - Count current Process Step accordion rows before clicking +Add.
  - Count Conditions/Actions grid rows before clicking nested +Add.
  - Only click +Add when desired row count is greater than existing row count.
- Stopped generic fill from handling deep repeatable rows:
  - Configure Target process-step fields are now filled only by row executor.
  - Configure Routing condition/action fields are now filled only by row executor.
- Expanded Process Step row scanning:
  - Scope from `dds-accordion-item` to owning `app-process-step` so Mapping Configuration controls are visible to the scanner.
  - Added positional fallback for Mapping Configuration child fields.
- Fixed duplicate routing Actions rows:
  - Action +Add no longer fires when default Actions row already exists.
- Strengthened TP screenshot recovery:
  - Phase summary screenshot paths and Windows paths are recovered by filename from the local run folder.

## MCP/LLM Position
No extra MCP server is required. The existing Chrome DevTools MCP/Playwright layer has the right primitives. The missing layer was effect validation:
- Before click: count rows/active controls.
- Click.
- After click: verify the expected row/control appeared.
- Only then fill.

Dell AIA / AutoGen remains planner/judge only. Deterministic MCP/Playwright is still the executor.

## Validation
`pytest -q` passed:

```text
204 passed, 2 warnings in 7.33s
```
