# HIP Portal V243R8 Verified Final Certification — 2026-09-22

## Scope
This certification was produced by re-extracting and re-verifying the R8 source package, then hardening the in-place updater so an existing installed `hip-agent` cannot silently remain on an older wheel.

## Full source test accounting
The exact R8 Python source/test tree collected **1,254 tests**. The monolithic run reached 86% before the shell execution window expired because some browser-style tests leave shutdown work behind. The same 166 test files were then partitioned into disjoint batches, with the one slow browser-startup file isolated. Final accounting:

- Passed: **1,253**
- Skipped: **1**
- Failed: **0**
- Collected/accounted for: **1,254 / 1,254**

Batch pass counts: 162, 149, 156, 157, 156, 168, 175, and 130 + 1 skipped.

## Syntax / package checks
- Python source compile check: PASS
- JavaScript syntax: **9 checked, 0 failed**
- Wheel `dist/` and `release/` copies: byte-identical
- R8 wheel contains `persistent_operator.py`, `recursive_self_improvement.py`, `trace_self_repair.py`, `native_hip_phase_mission.py`, `phase_vocabulary_learning.py`, and `human_phase_review.py`
- `operate-hip` CLI command present in wheel/source
- Clean wheel `--target` install and imports: PASS

## R8 convergence defaults verified
- `persistent_operator.max_goal_cycles = 0`
- `persistent_operator.require_final_human_confirmation = true`
- `persistent_operator.human_wait_seconds = 0`
- `recursive_self_improvement.max_recursive_cycles = 0`
- Source-code self modification remains disabled

## Post-packaging hardening
The in-place updater now:
1. Preserves `.env`, `config.yaml`, `input.json`, `runs`, `data/hip_memory`, `.backend_runtime`, and `.hip_runtime`.
2. Backs up replaced files under `.hip_patch_backups`.
3. Copies the R8 source over the existing folder.
4. Force-reinstalls the exact bundled R8 wheel using `python -m pip install --force-reinstall --no-deps` unless explicitly skipped.
5. Smoke-checks R8 imports and verifies `operate-hip` is visible.

The Python/backend/frontend/test tree was hash-compared before and after this updater hardening and is byte-identical; only deployment/documentation packaging was changed after the full-suite run.
