# HIP Portal Agent v1.9.5 — Continuous Surface Provenance Lease

## Problem fixed

v1.9.4 correctly binds a detached Angular/CDK/DDS overlay to the scoped opener that created it. The next live failure class occurs when that already-proven overlay is destroyed/recreated during asynchronous loading, filtering, virtual scrolling, or portal rerender. A selector that pointed to the correct menu at time T0 is not durable identity at T1. A stale duplicate surface may also remain visible.

## Runtime rule

Provenance is now a lease, not a one-time selector decision. Before every target resolution and every virtualized scroll, the runtime snapshots visible actionable surfaces and re-proves the leased surface. Rebinding prefers opener `aria-controls` / `aria-owns` identity, then exact surface ID continuity, then a unique structural continuation using surface kind/role, accessible label/title, action overlap and geometry. Selector equality contributes only weak evidence.

If two visible surfaces are similarly plausible, execution fails closed.

## Stable target guard

A surface-scoped target must resolve consistently across consecutive semantic reads before being returned to the compound-action executor. The lease is refreshed between those reads, so an overlay may be safely rebound if Angular recreated it.

Immediately before click, the executor re-proves the surface once more and checks that exactly one visible target match exists and that it is contained by exactly that one proven surface. A same-named global action causes the membership gate to fail.

## Governance

This layer does not expand mutation authority. Clone/Migrate/Deploy/Delete/Save/Create remain governed by the existing authorization path. AutoWebGLM remains the primary browser-decision framework and the deterministic Playwright/DDS layer remains the effect-verified executor.

## Regression

- New v1.9.5 failure-focused tests: 4
- Complete suite after implementation: 749 / 749 PASS
