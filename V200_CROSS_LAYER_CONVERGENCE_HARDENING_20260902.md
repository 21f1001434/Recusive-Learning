# HIP Portal Agent v2.0.0 — Cross-Layer Convergence Hardening

## Purpose

This release closes interaction gaps that appear only when otherwise-correct layers overlap: parallel agents, background network traffic, process restart, SSO/route drift after planning, SPA navigation with stale overlays, and generic self-heal replay after a certified mutation outcome. AutoWebGLM remains the primary browser policy framework; deterministic browser/MCP code executes its aligned intent under the existing governance gates.

## Layer A — dispatch causality fence

Every physical click dispatch records the request IDs already known to CDP, route, stage and network index. Mutation verification/reconciliation accepts only write requests first observed after that boundary and on the same HIP route/stage. This prevents a background audit/autosave request that happened during preflight from being credited to a later Deploy/Create click. Pending CDP write requests are also surfaced before response-body capture completes.

## Layer B — in-session mutation serialization and quarantine

Mutation dispatch is serialized with one browser-session lock. At the instant a write-capable physical click is attempted, the session arms a value-free mutation quarantine. Any other mutation is blocked until reconciliation classifies the first action as `not_dispatched`, `rejected_verified`, `committed_verified`, or `committed_verified_after_transport_or_ui_error`. Indeterminate, partial, pending and visible-success-unconfirmed outcomes keep the quarantine armed.

## Layer C — cross-run unresolved mutation quarantine

Governed LIVE execution persists `change_execution_started` before entering the certified executor. If the process dies before a terminal ledger event, or the terminal event requires manual review because a mutation may have partially committed, an identical idempotency key is blocked on later runs. The normal force-repeat option may override a prior verified success but cannot override an unresolved prior execution.

## Layer D — parallel-safe governance ledger

Ledger append now uses a bounded cross-process lock file, re-reads the chain only after lock acquisition, flushes and fsyncs the append, and reclaims a stale lock after a bounded interval. This prevents two parallel agents from deriving the same previous hash and forking the audit chain.

## Layer E — final pre-dispatch route/auth/locator barrier

After the normal universal locator preflight, interactability wait and AutoWebGLM decision, mutation actions perform a second final boundary check. An SSO redirect, route drift or locator drift at that point fails before `_mark_click_dispatch`, keeping the outcome `not_dispatched`.

## Layer F — route-bound surface ancestry

Nested semantic surface proofs now carry the HIP route on which they were established. If Angular client-side navigation moves to a different route, the entire chain is invalidated before lease refresh or continuation resolution. A dialog/menu proof can never cross page-family/module boundaries.

## Layer G — self-heal / mutation-policy convergence

The generic ReAct self-heal classifier explicitly treats certified mutation quarantine, outcome, response-lost, rejected and automatic-retry-prohibited errors as `unsafe_or_mutating`. Therefore a browser recovery plan cannot replay an already-dispatched or policy-final mutation phase.

## Safety invariants

- The agent cannot edit canonical payload values; only explicit human editing can change them.
- No mutation executor fallback occurs after physical dispatch.
- No concurrent mutation occurs while a prior outcome is unresolved.
- No cross-run identical mutation replay occurs while a prior execution is unresolved.
- No background/pre-dispatch write can count as the requested mutation result.
- No nested UI provenance survives navigation to a different HIP route.
- No generic self-heal action can convert a certified mutation no-retry decision into a retry.
- Customer-entered values are not written into the new quarantine/causality metadata.

## Verification

The source tree passes 767/767 automated tests, including the new v2.0.0 cross-layer hardening tests and all prior semantic/overlay/mutation regression suites. Live Dell SSO/HIP tenant/Edge policy/Dell AIA behavior still requires the authenticated Dell Windows environment.
