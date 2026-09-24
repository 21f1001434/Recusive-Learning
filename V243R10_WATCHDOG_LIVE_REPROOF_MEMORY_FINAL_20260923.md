# V243R10 — Watchdog Live Reproof + Persistent Learning

## Why R10 exists

A live Dell HIP run could reach a stable, correctly filled Data Map but still be stopped by `HIP_PHASE_NO_PROGRESS_WATCHDOG`. The watchdog previously checked only the saved target-branch execution artifact. If the long-running KB/learning coroutine had not emitted that artifact yet, a stable correct form was classified as "no progress" even though the browser state itself was already complete.

R10 adds an evidence-first terminal reproof and makes persistent learning auditable.

## Terminal read-only live reproof

Before a no-progress watchdog declares a phase incomplete, the runtime now performs one read-only proof against the current browser state:

1. Capture current HIP stateful controls from the active form.
2. Compile the exact semantic expectation from the current phase input.
3. Compare every nonblank input-owned field using deterministic exact readback.
4. Verify repeatable row counts/row identities.
5. Verify required file inputs from `input.files`/current file-control state.
6. Reject the proof if current relevant controls are `aria-invalid=true`.
7. Persist only a value-minimal structural receipt; no selector/coordinate becomes reusable authority.

If the live reproof passes, the watchdog classifies the stable state as `HIP_PHASE_EXACT_STATE_POST_COMPLETION_STALL`, cancels only the extra post-fill learning/reporting work, and continues to verification/judge/handoff without replaying or refilling the form.

If the live reproof fails, normal self-heal/HITL remains active and the same phase stays authoritative.

## Human Looks Correct behavior

When a human chooses **Looks correct** but the earlier saved exact artifact is stale or missing, R10 first performs the same live read-only terminal reproof. Human approval never bypasses missing fields, failed uploads, wrong row counts, or live invalid state. When live proof succeeds, the phase can be accepted without another destructive fill attempt.

## Stale proof protection

`phase_live_read_only_reproof.json` is attempt-scoped. The file is deleted at the beginning of every new execution/repair attempt, so a proof from a previous browser state cannot satisfy a later checkpoint.

## Persistent learning / memory

The agent does retain reusable HIP knowledge, but it separates **candidate learning** from **validated learning**:

- Failed/blocked attempts can add candidate/negative evidence used for recovery.
- Exact + judged/human-approved success can promote structural knowledge to trusted memory.
- Customer business values are not promoted as reusable portal knowledge.

Persistent stores remain under `reporting.memory_dir` (normally `./data/hip_memory`) and include the Portal Brain, capability graph, flow-pattern memory, replay/Dreaming policy, deterministic recipes, model portfolio, human teaching, and RSI state.

Every successful phase now writes `phase_learning_memory_receipt.json`, showing the persistent memory root and the structural memory/promotion status with `customer_values_persisted=false`.

## Completion contract

A phase can hand off only after current live proof and the configured judge/human gates succeed. Post-completion learning quality may affect recipe/memory promotion, but it must not reopen a completed phase.

## In-place upgrade

For an existing folder, use the R10 in-place package. It preserves:

- `config.yaml`
- `.env`
- `input.json`
- `runs/`
- `data/hip_memory/`
- `.backend_runtime/`
- `.hip_runtime/`

The updater backs up replaced source, copies R10, force-reinstalls the exact bundled wheel with `--no-deps`, and verifies `operate-hip` plus the R10 live-reproof module.

## Validation boundary

Automated tests validate source/package contracts and local browser behavior. Dell tenant SSO, tenant-specific Angular/DDS changes, live Dell AIA availability, and authorized Save/Edit/Deploy/Migrate effects remain real-environment validation points.
