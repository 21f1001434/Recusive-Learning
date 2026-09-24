# HIP Agentic ReAct + KB + Judge Runtime Fix — 2026-07-17

## Supplied run

Run: `UHAUL-POASN-FULL-DUMMY-20260717-013312`

Data Map completed. The persistent session then attempted to start Source Document Type. Python Playwright, official Playwright MCP, and Chrome DevTools MCP all still observed the Data Maps route. The old gate treated this as an MCP same-surface failure even though all three executors agreed with each other. The requested Document Types route simply had not committed.

A second defect then raised `NameError: mask_sensitive_string is not defined`, hiding the original recoverable route failure.

## Root architectural defect

The previous runtime conflated:

1. executor agreement on the current actual page;
2. commitment of the requested target page.

These are independent gates. Agreement on the wrong page must produce a new navigation action, not an MCP-failure verdict.

The previous target usability detector could also accept another authenticated HIP module because generic portal text was treated as evidence for the requested module.

## Implemented architecture

### Bounded navigation ReAct controller

Every phase transition now executes:

1. **Plan** — load the validated phase URL and expected surface terms from the HIP navigation KB.
2. **Act** — reuse the current authenticated context and navigate through official Playwright MCP first, with bounded local Playwright fallbacks.
3. **Observe** — collect Python Playwright URL, Playwright MCP URL, Chrome DevTools MCP page, SSO state, target path, page text, and Angular readiness.
4. **Judge** — independently evaluate target commitment, target usability, and executor agreement.
5. **Repair** — retry navigation, clean transient UI, consolidate tabs, force a route commit, or resynchronize MCPs according to the observed failure class.

The loop is bounded and fail-closed. It does not click final mutation actions.

### Separate gates

- `same_actual_surface`: all executors see the same actual browser page.
- `target_match`: the actual browser page is the requested phase URL.
- `target_usable`: the requested module path and module-specific surface terms are present.

All three are required before a phase begins.

### Strict target identity

A Data Maps page can no longer satisfy a Document Types target merely because the user is logged in or generic portal text is visible. The requested path must match exactly.

### Persistent-session recovery

- Wrong route, executors agree: navigate/replan in the same context.
- Correct route, MCPs disagree: resynchronize MCPs.
- Genuine SSO page: request SSO once and preserve the context.
- Mid-run session expiry: replay only the interrupted phase once.

### Phase exception recovery

`dummy_fill_e2e.py` now imports `mask_sensitive_string` and classifies:

- `HIP_ROUTE_NOT_COMMITTED`
- `HIP_MCP_SURFACE_DRIFT`
- `HIP_AUTH_SESSION_EXPIRED`

Route and MCP failures are retried without SSO. Only actual authentication expiry may prompt reauthentication.

## ReAct evidence

Each phase writes:

`mcp_runtime/navigation_react_trace.json`

The artifact contains concise, auditable plan/act/observe/judge records and no hidden chain-of-thought.

## Relationship with form learning

The navigation ReAct controller complements the existing form runtime:

- current `input.json` phase-local plan;
- validated Portal Brain and Unified KB edges;
- parent-before-child state graph execution;
- DOM events and mutation observation;
- exact committed-value reconciliation;
- deterministic, text, and vision judges;
- bounded targeted repair;
- judge-approved fast replay.

## Verification

- Python compilation: passed.
- New ReAct navigation tests: 4 passed.
- Existing persistent session, SSO redirect, and dual-MCP tests: passed.
- Complete test suite: 313/313 passed.

No authenticated Dell HIP live rerun was possible in this environment.
