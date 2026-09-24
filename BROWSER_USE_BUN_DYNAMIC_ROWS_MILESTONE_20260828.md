# HIP Portal Agent — Browser-Use + Bun + Dynamic Repeatable Rows

Release: 1.5.0  
Validation date: 2026-08-28

## Implemented

- Browser-Use 0.13.8 is integrated as a same-browser CDP state/recovery layer.
- Browser-Use attaches to the existing persistent HIP Chrome session and therefore reuses Dell SSO/browser state.
- Browser-Use does not run an unrestricted mutation agent; governed Playwright/MCP execution remains authoritative for state-changing actions.
- Bun is the default JavaScript/MCP package manager and runtime surface.
- `package.json` pins `@playwright/mcp` 0.0.79 and `chrome-devtools-mcp` 1.7.0.
- Config defaults use `bunx --bun`, not `npx`.
- Input-driven repeatable row planning derives required rows directly from input JSON.
- Section-local `+` controls are resolved deterministically first, then through a broader structured element inventory fallback.
- Every `+` click is effect-validated and accepted only for an exact N -> N+1 row-count transition.
- Ambiguous/global plus controls are refused.
- If the portal already contains more rows than the input requires, destructive automatic removal is refused.
- Browser-Use structured state is attached to repeatable-row recovery evidence when deterministic row/add discovery cannot safely proceed.
- Streamlit Preflight now displays the input-driven `+` row plan before execution.

## Two-row behavior

When a section contains two JSON rows and HIP initially displays one row:

1. Fill/validate the existing first row.
2. Resolve the correct section-local `+` control.
3. Click `+` exactly once.
4. Require visible row count 1 -> 2.
5. Fill the second row using the second JSON object.
6. Verify final row count and section values before continuing.

For the shipped U-HAUL example, tests verify Rule Conditions, Flow Identifier Conditions, Process Steps, Routing Conditions, and multi-row Document Type Attributes are all detected from the actual input JSON.

## Validation

- Full automated suite: 632/632 passed.
- Python compileall: passed.
- JSON/YAML parsing: passed.
- Python wheel build: passed (`hip_portal_id_agent-1.5.0`).
- Live Dell SSO/HIP tenant execution is environment-specific and must be performed on the authenticated Dell machine.
- Bun and a live Chrome/CDP surface are not installed/exposed in the packaging sandbox, so live `bun install`/MCP/browser-use attachment cannot be executed here; the shipped Bun configuration is statically validated and covered by tests.
