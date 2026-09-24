# Autonomous Web Agent Mission Controller — 2026-07-21

## Objective

Complete the application as one continuous autonomous objective. The
until-complete package already self-healed inside each phase, but three
mission-level gaps kept the agent from being truly autonomous end-to-end:

1. **No resume.** An interrupted or crashed until-complete run (Ctrl+C, power
   loss, machine reboot) restarted all seven phases from zero, replaying phases
   that had already passed the deterministic exact-state lock and both
   independent judges.
2. **No browser-death recovery.** "Browser has been closed / disconnected"
   errors were classified as generic timeouts, and no safe action could
   relaunch Chrome, so a dead browser process ended the whole mission.
3. **No mission verdict or cross-phase entity consistency.** Nothing declared
   "application complete" as one authoritative artifact, and later phases did
   not see the entity names used by earlier completed phases.

## What was added

### 1. `hip_id_agent/mission_controller.py` (new)

- **Crash-safe mission ledger** — `mission_state.json` in the run root records
  every phase as pending / in_progress / complete / blocked with attempt
  counts, judge outcomes and timestamps. It is rewritten after every
  transition, so the ledger survives any interruption.
- **Fail-closed resume adoption** — `adopt_completed_phases(prior_run)` adopts
  a prior phase only when ALL of the following exist in the prior run:
  - `phase_exact_state_lock.json` with a passing exact-completion checkpoint,
  - `section_judge_gate.json` with `pass: true`,
  - the persisted `phase_verification.json` payload,
  - and no unresolved `section_judge_block_diagnosis.json`.
  Anything less re-executes the phase live. Adoption never replays the
  browser; it materializes `resumed_from_prior_run.json` plus the prior
  verification/judge payloads in the new run's phase directory.
- **`find_resumable_run(runs_dir)`** — locates the newest prior run whose
  mission is incomplete and holds at least one adoptable phase.
- **Per-run entity registry** — `mission_entity_registry.json` records the
  dummy display names each judged phase actually used. It is value-scoped to
  the run directory and is never promoted into the long-term portal brain,
  preserving the value-free memory policy. After each successful phase, the
  remaining phase input files receive `_mission_prior_entities` so all later
  phases reference one consistent entity set.
- **Final mission verdict** — `mission_completion_report.json` and
  `mission_completion_report.md` declare `application_complete: true` only
  when every selected phase completed with deterministic proof and passing
  independent judges (in this run or adopted fail-closed from a resumed run).

### 2. Browser-death autonomous recovery

- `runtime_self_heal.py` gains the `browser_disconnected` classification
  (browser closed / disconnected / crashed / driver-pipe patterns) with the
  safe action ladder `restart_browser_session → recover_page_and_route`.
- `browser_session.py` gains `BrowserSession.restart()`: it relaunches the
  same persistent user-data-dir Chrome context (existing Dell SSO cookies are
  reused; expired sessions fall into the normal re-authentication prompt),
  recycles the MCP attachments, preserves session counters and learned-memory
  references, and never mutates the portal. Older sessions without `restart`
  fail closed inside the action executor.

### 3. Flow, CLI and runner wiring

- `FullDummyFillOptions` gains `resume_run_dir` and `auto_resume`.
- `FullDummyFillE2EFlow.run` creates the mission controller, performs
  adoption before the shared browser opens a resumed phase, skips adopted
  phases without browser replay, records every attempt/success/block into the
  ledger, persists `phase_verification.json` + `phase_judge_result.json` on
  every judged success (making this run adoptable by future runs), and writes
  the final mission verdict into the aggregate report under
  `autonomous_mission`.
- CLI: `--resume-run <dir>` and `--auto-resume` on `run-full-dummy-fill`
  (mutually exclusive; the directory must exist).
- `RUN_ALL_PHASES_UNTIL_COMPLETE.ps1`: `-Resume` (auto) and
  `-ResumeRunDir <path>`.

## Safety invariants preserved

- Adoption is strictly fail-closed; a phase without complete triple proof
  re-executes live.
- `restart_browser_session` is inside the safe-action allow-list; the
  no-save/no-mutation policy and the mutating-word blocklist are untouched.
- The entity registry stays inside the run directory; long-term flow-pattern
  memory and the portal brain remain value-free.
- The independent deterministic + text + vision judges remain the only path
  to marking a phase complete.

## Tests

- New `tests/test_autonomous_mission_until_complete.py` — 14 tests covering
  the ledger lifecycle, blocked-phase verdicts, fail-closed adoption proof,
  partial adoption with skips, newest-resumable-run discovery, entity registry
  carry-forward and extraction (including secret exclusion), browser
  disconnect classification, safe action ladder, restart action execution and
  legacy fail-closed behavior, and flow/CLI wiring.
- Existing classification behavior regression-checked: control-not-found,
  unsafe mutation, false-loading, blocking-overlay, transient timeout, and
  authentication-expired messages keep their previous classifications.
