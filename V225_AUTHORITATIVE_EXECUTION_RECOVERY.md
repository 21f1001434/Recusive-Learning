# v2.2.5 — Authoritative Execution + Earliest-Stage ReAct Recovery

Date: 2026-09-09

## Live failure addressed

The v2.2.4 live run could reach/open a HIP create surface but still record zero business-field fills. The root cause was that Data Map, Rules and Transport Profile only entered their authoritative state-graph execution block when a legacy module-specific control inventory was non-empty. Those collectors can miss DDS/web-component/shadow-DOM controls even when the form is visibly open. The phase could then return and be blocked later by the independent section judge, hiding the actual execution failure.

## v2.2.5 behavior

The live execution order is now mandatory and fail-closed:

`form opened -> controls discovered -> semantic controls bound -> writable fields filled/exactly verified -> exact execution checkpoint -> independent section judge`

### Authoritative state-graph execution

- Data Map, Rules and Transport Profile no longer use `if self.fill_dummy and controls` as the gate for state-graph execution.
- `execute_phase_state_graph()` runs whenever the canonical input graph contains executable nodes.
- Every live phase invocation uses `strict_live_execution=True`.
- BizFlow uses the same strict contract per verified tab/section and aggregates the execution-stage proof across all tabs.

### Stateful Playwright / shadow-DOM discovery

- Module-specific inventories are merged with `capture_stateful_controls()`.
- The stateful collector starts from the active form root.
- If the active-root locator yields zero controls, it performs a Playwright page-level fallback. Playwright's locator engine can pierce open shadow DOM; semantic section/field/row binding still decides which controls are authoritative.
- A listing Search field cannot satisfy an unrelated form field because semantic confidence, section and repeatable-row identity remain mandatory.

### Earliest-stage failure codes

Strict execution raises specific recoverable failures before any independent judge:

- `HIP_FORM_CONTROLS_NOT_DISCOVERED`
- `HIP_FORM_CONTROLS_NOT_BOUND`
- `HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED`
- `HIP_PHASE_EXACT_EXECUTION_NOT_VERIFIED`

The runtime self-healer classifies these as `active_surface_lost`, returning through the existing bounded ReAct route/form recovery path rather than retrying the final judge.

### Independent judge gate

`FullDummyFillE2EFlow` now checks `phase_exact_completion_checkpoint()` before `judge_artifact_section()` can run. If exact browser execution is absent, the judge is not invoked and the phase recovers from the execution stage instead.

The checkpoint also respects the executor's authoritative `failed_attempts` list, so optional/conditional informational attempts do not incorrectly invalidate an otherwise passing exact execution.

### Mission Step Trace

The live trace now reports the execution pipeline separately:

- form opened
- controls discovered
- controls bound
- fields filled/verified
- exact execution verified

This makes the earliest failed stage visible instead of presenting only a later generic section-judge BLOCK.

## Preserved architecture

- Chrome-primary persistent Dell SSO browser, Edge/Playwright Chromium startup fallback.
- AutoWebGLM/ReAct planning and bounded recovery.
- Playwright MCP governed web execution.
- Chrome DevTools MCP independent witness.
- HIP Intelligence MCP semantic website brain.
- Gemma vision only under the established evidence/ambiguity policy.
- PyAutoGUI MCP governed native/desktop fallback only.
- v2.2.2 native/uncapped Dell AIA output behavior.
- v2.2.3 Windows UTF-8 and semantic judge hardening.
- v2.2.4 mandatory `+ Add` / BizFlow template-link / tab-open proof.
