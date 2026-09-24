# HIP Portal V237 / v2.3.7 — Runtime Input + Golden Reference Fill Hardening

## What changed

- The actual runtime `input.json` is enumerated leaf-by-leaf on every adaptive fill cycle.
- Any nonblank runtime input leaf missing from the handwritten phase graph is reconciled against the live controls.
- A unique live semantic match creates a transient execution node using label/name/framework key/section/row identity; selectors and coordinates are never persisted.
- Newly revealed input-owned controls force a subsequent execution cycle before a phase may pass.
- BizFlow input accounting is tab-aware across Flow Details, Configure Source(s), Configure Target(s)/Process Steps, and Configure Routing.
- Golden screenshots are attached to the shared browser session and used proactively as visual structure guidance for binding repair.
- Golden screenshots never supply customer values; `input.json` remains the only value authority.
- If a runtime input leaf cannot be uniquely bound, the phase fails closed with the exact JSON path and candidate labels instead of silently skipping it.

## Source certification

- Collected: 1,182 tests
- Passed: 1,181
- Skipped: 1
- Failed: 0
- Local Chromium mission: 7/7 phases PASS
- BizFlow lifecycle: Edit / Save / Validate / Deploy PASS
- Dell environment contacted: false

## Important runtime invariant

A phase may pass only when every applicable nonblank input-owned value has either an executable/verified binding or an explicit canonical alias. A newly discovered runtime node is not considered complete until it is executed and exact-readback verified in a later cycle.
