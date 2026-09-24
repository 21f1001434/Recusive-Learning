# HIP Portal Agent v1.9.6 — Nested Surface Ancestry Chain

## Problem

A correctly proven row/menu action can open a detached child dialog, which can in turn open another drawer, picker, listbox, or confirmation surface. v1.9.5 continuously re-proved one surface, but provenance was not automatically carried into the next surface. A later `Save`, `Create`, `Next`, or `Close` step could therefore lose row/entity scope or encounter a same-named global control.

## Runtime contract

1. Re-prove the current parent surface before a continuation action.
2. Resolve the target only inside the deepest proven active surface.
3. Immediately before click, prove unique target membership in that exact surface.
4. After click, compare structural surface snapshots.
5. If one child surface is proven to be newly opened by the action, inherit bounded parent provenance and push the child onto the active surface chain.
6. If no child opens, prune only closed child surfaces and re-prove the nearest still-visible parent.
7. While a proven child remains active, never fall back to a same-named global page action.
8. Reset the chain on module navigation.

## Stored evidence

The ancestry stores structural fields only: selector hint, stable ID when available, role/kind, bounded geometry and parent structural identity. It does not persist typed customer values. Maximum chain depth is 8.

## Verification

Focused semantic compatibility and v1.9.6 failure-focused tests: 20 / 20 PASS.
Full regression after implementation: 753 / 753 PASS.
