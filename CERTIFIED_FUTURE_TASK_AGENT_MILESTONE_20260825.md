# Certified Future Task Agent — 2026-08-25

## Objective

Turn the learned HIP Capability Graph into a safe general-purpose browser agent for future natural-language requests. The agent must exploit certified replay first, recover from UI drift without inventing capabilities, preserve API/UI evidence, and fail closed for ambiguous mutation outcomes.

## Implemented architecture

1. **Certification gate** — derives live family readiness from `HIPCapabilityCertifier` before execution.
2. **Cross-family task decomposition** — one natural-language request can reference Data Maps, Document Types, Rules, Transport Profiles and BizFlows in sequence.
3. **Certified deterministic replay** — verified family replay is preferred when available.
4. **Certified capability exploitation** — learned semantic capabilities are used when no exact replay exists.
5. **Guarded adaptive recovery** — when a selector/capability drifts, the runtime captures the current DOM surface, learns the drifted structural state, semantically rebinds the intended action, and can use AutoGen 0.7.5 to rank only observed candidate controls.
6. **Mutation ambiguity rule** — Deploy/Delete/Migrate/etc. are never automatically retried after click invocation. A retry could double-apply a mutation when the backend outcome is unknown.
7. **Three-MCP assurance** — successful family execution requires fresh Playwright MCP, Chrome DevTools MCP and HIP Intelligence representation evidence.
8. **UI→API causal trace** — each executed capability is associated with exact request IDs, method, endpoint, request payload, response status and response payload when observable.
9. **Cross-family task replay** — a successful task is promoted to a value-free task replay containing capability IDs, page families and runtime value sources such as `current_task.entity`, never the actual customer/map/rule/profile value.

## New public interfaces

- `plan-certified-task`
- `run-certified-task`
- `POST /api/certified-task/plan`
- `POST /api/certified-task/run`
- `GET /api/certified-task/replays`
- Streamlit **Certified Future Task Agent**
- `RUN_CERTIFIED_FUTURE_TASK.ps1`

## Capability Graph v7

`hip.capability-graph.v7` adds `task_replay_profiles` for verified cross-family future-task trajectories. Existing page, capability, API and family replay memory remains backward-compatible during load.

## Safety invariants

- AutoGen cannot invent executable capability IDs.
- Adaptive recovery can only act on controls observed in the current live DOM.
- A mutation candidate cannot satisfy a non-mutation request.
- Mutation execution still requires explicit task permission + `HIP_ALLOW_PORTAL_MUTATION=YES` + exact phrase `ALLOW HIP MUTATION`.
- A mutation is not automatically retried after click invocation.
- Persistent replay stores no current task entity values.
