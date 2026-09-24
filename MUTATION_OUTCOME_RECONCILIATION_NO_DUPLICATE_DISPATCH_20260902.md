# HIP Portal Agent v1.9.7 — Mutation Outcome Reconciliation + No Duplicate Dispatch Guard

## Problem closed

A browser automation exception does not prove a portal mutation failed. A click can reach HIP, trigger a POST/PUT/PATCH/DELETE, and then time out while Angular waits for the response. Retrying through another executor can create duplicate objects or repeat deployment. Conversely, a selector/preflight failure before a physical click is not a backend-ambiguous mutation.

## Runtime contract

1. Before a mutation click, BrowserSession records `pre_dispatch`.
2. Immediately before the physical MCP/Playwright/PyAutoGUI click call it records `dispatching`.
3. If that call returns, it records `dispatch_returned`; if it raises, it records `dispatch_exception`.
4. Once `dispatch_attempted=true`, no second executor is allowed to click the mutation.
5. The certified executor performs bounded read-only reconciliation over network evidence and visible structural status signals.
6. `2xx` write with no failed write => committed verified, including when the browser action raised.
7. terminal `4xx/5xx` write => rejected verified; no automatic retry.
8. visible success without `2xx` => network-unconfirmed success; no retry and manual review.
9. pending/no-response write, dispatch with no terminal write, redirect-only write, or mixed successful/failed writes => fail closed/manual review.
10. Only `not_dispatched` may perform one fresh semantic rebind. No backend mutation has been sent in that state.

## Existing protections retained

AutoWebGLM primary policy, AgentQ gating, mutation authorization, semantic affordances, repeatable-row identity, detached-overlay provenance, continuous surface leases, nested child-surface ancestry, post-change MCP/API assurance and the hash-chained change ledger remain active.

## Privacy boundary

Reconciliation does not store customer-entered values or raw toast text. Visible outcome evidence is reduced to keyword classes and hashes.

## Verification

Full regression after implementation and version promotion: 757 / 757 PASS.
