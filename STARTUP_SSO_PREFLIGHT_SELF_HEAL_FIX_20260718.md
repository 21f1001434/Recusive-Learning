# Startup SSO Preflight + Self-Heal Fix — 2026-07-18

## Supplied failure

The all-phase runtime started from `about:blank`, correctly navigated to the Data Maps URL, and reached Dell SSO. The navigation ReAct controller returned a structured `sso_required` state. `RuntimeSelfHealController.prepare_phase_attempt()` incorrectly treated every non-pass route result as `HIP_ROUTE_NOT_COMMITTED`, so the run stopped before the user could complete SSO.

A second orchestration defect made this fatal: `prepare_phase_attempt()` was outside the phase `try/except`, so preflight failures bypassed the bounded runtime self-heal loop.

## Corrected behavior

1. The preflight route controller observes `sso_required`.
2. It delegates to the persistent-session `goto_base_and_complete_sso()` coordinator.
3. The user completes Dell SSO once in the same Chrome context.
4. The target route is re-observed and independently verified.
5. Data Map attempt 1 proceeds without restarting the phase or browser.

The route result is recorded as `hip.navigation-react-sso-resume.v1` with both pre-SSO and post-SSO evidence.

## Self-heal boundary

`prepare_phase_attempt()` and phase execution now share one exception boundary. A recoverable preflight failure is classified, captured, repaired and retried by `RuntimeSelfHealController`, just like a form execution failure. `phase_execution_attempts.json` records `failure_stage` as either `agentic_preflight` or `phase_execution`.

## Safety

The patch does not weaken exact target verification, dual-MCP synchronization, no-save controls, bounded retries or fail-closed behavior.

## Verification

- Python compilation: passed
- Startup SSO/preflight regressions: passed
- Full suite: 340 passed
- Authenticated Dell HIP live run: not performed in this environment
