# V243R10 Final Source Certification — 2026-09-23

## Release identity

- Release label: **V243R10 — Watchdog Live Reproof + Persistent Learning**
- Python package compatibility version: **2.4.3**
- Wheel SHA-256: `d75d2c89cd488b72adc84b4b63426524259ed4518d18dce8835e2a6940090a65`

## Defect addressed

A stable, correctly filled HIP phase could be blocked by `HIP_PHASE_NO_PROGRESS_WATCHDOG` when the long-running phase-learning coroutine had not yet emitted its saved exact execution artifact. R10 adds a **terminal read-only live browser reproof** before the watchdog declares no progress.

If current live controls, row state, validation state, and required upload state exactly satisfy the current phase input, the watchdog converts the condition to a post-completion stall and allows judge/handoff to continue without refilling the phase. If live proof fails, the phase remains blocked and self-heal/HITL continues.

## Persistent learning

Reusable structural learning is persisted under `reporting.memory_dir` (normally `./data/hip_memory`). Persistent stores include Portal Brain/capability graph, flow-pattern memory, replay/Dreaming policy, deterministic recipes, model portfolio state, human teaching, and RSI state.

Blocked/failed attempts can contribute candidate or negative evidence. Trusted deterministic knowledge is promoted only after exact live proof plus the configured judge/human acceptance gates. Customer business values are not promoted as reusable portal knowledge.

Successful phases now write `phase_learning_memory_receipt.json` so memory persistence/promotion is inspectable.

## Source-level verification

The complete R10 source suite was collected and executed in disjoint test-file batches to avoid known browser/process teardown delays in one monolithic pytest invocation.

- Tests collected: **1,263**
- Passed: **1,262**
- Skipped: **1**
- Failed: **0**
- Test files: **168**

Focused R10 regressions:

- R10 live-reproof/watchdog/memory tests: **5 passed**
- Combined R10/R9/watchdog/Data Map/HITL/self-learning regression: **69 passed**
- Persistent-memory regression set: **39 passed**

Additional source checks:

- Python compilation: PASS
- Wheel build: PASS using offline `--no-build-isolation`
- Clean wheel install with `--no-deps`: PASS
- `operate-hip` CLI registration: PASS
- Wheel includes `phase_live_reproof.py`, `phase_progress.py`, `dummy_fill_e2e.py`, persistent operator and RSI modules: PASS

## Safety / completion behavior

Human `Looks correct` cannot bypass a genuinely missing required field, failed upload, wrong repeatable-row structure, or live invalid state. It can reconcile a stale/missing execution artifact only when a fresh read-only live reproof proves the current form exactly.

The live reproof is attempt-scoped and is invalidated before each new execution/repair attempt.

## Validation boundary

This certification validates source/package contracts and local/mock/browser-test behavior. It does not claim a real Dell tenant was mutated. Live Dell SSO, tenant-specific Angular/DDS behavior, Dell AIA availability, and authorized Save/Edit/Deploy/Migrate operations remain authoritative live-environment checks.
