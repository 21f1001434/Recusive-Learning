# HIP Portal V243R7 — Completion-First Phase Gate

## Why this release exists
The live Data Map run showed a critical completion-control failure: text/dropdown fields could be filled while the required Map Data upload was not committed, and once bounded self-heal stopped the shared browser could close and the run could emit partial/final-looking artifacts.

V243R7 changes the mission contract from **attempt-oriented** to **completion-oriented**.

## Mission invariant
For every selected HIP phase:

1. Stay on the current phase.
2. Achieve exact input-owned state.
3. Prove required file uploads and repeatable rows.
4. Run deterministic verification.
5. Run judge reconciliation / HITL when configured.
6. Mark the phase complete.
7. Only then hand off to the next phase.

No downstream phase starts while the current phase is incomplete.

## Data Map completion contract
A Data Map phase cannot pass unless the required `map_data_file` has an exact upload proof. The proof must show a successful, exact upload attempt with a committed filename and an authoritative phase-specific uploader.

The uploader now:
- treats an explicit file path in `input.json` as authoritative;
- resolves relative paths against the current working directory and input JSON directory;
- re-resolves hidden DDS `input[type=file]` controls after Angular parent-field rerenders;
- uses the semantic uploader first;
- falls back once to the phase-aware stable Data Map file-input driver (`mapData` / active form root);
- never uses upload success as permission to click Save/Create.

## Incomplete-phase browser hold
If autonomous repair reaches its bounded safety/no-progress limit, V243R7 does not terminate the mission by default.

Instead it writes `INCOMPLETE_PHASE_WAITING.json`, creates an `incomplete_phase_recovery` request in the existing Human Phase Review store, keeps the same browser/session alive, and waits.

The operator can inspect or manually correct the live form and then click either review button. The button is only a **resume/recheck signal**. The agent must re-prove the live phase; it does not treat human approval as completion evidence.

Default configuration:

```yaml
human_in_the_loop:
  hold_browser_on_incomplete_phase: true
  incomplete_phase_wait_seconds: 0   # 0 = wait indefinitely
  incomplete_phase_poll_seconds: 2.0
  keepalive_seconds: 20.0
  never_finalize_incomplete_run: true
```

## No half-complete final artifact
A blocked mission is not a final result. If the operator explicitly configures a finite hold or disables the hold, V243R7 writes only `INCOMPLETE_RUN_CHECKPOINT.json` and returns `incomplete_checkpoint_only`. It does not generate the normal FINAL HTML/CSV/summary ZIP for a partial mission.

## Phase-by-phase execution
The native phase engine remains authoritative:

- Data Map — `DataMapKBFlow`
- Source Document Type — `DocumentTypeKBFlow`
- Target Document Type — `DocumentTypeKBFlow`
- Rules — `RuleKBFlow`
- Source Transport Profile — `TransportProfileKBFlow`
- Target Transport Profile — `TransportProfileKBFlow`
- BizFlow — `BizFlowKBFlow`

The Universal Portal layer is fallback/recovery, not the primary executor for these known HIP forms.

## Learning and deterministic replay
The R4–R6 learning stack remains enabled:
- capability graph / Portal Brain;
- phase vocabulary learning;
- trace self-repair;
- multi-model champion/challenger routing;
- Human field teaching and phase review;
- replay/dreaming policy;
- deterministic recipe promotion only after repeated verified success.

A learned path is still live-reproved before use; brittle screen coordinates/selectors are not persisted as long-term knowledge.

## Verification summary
Focused phase verification in this release:
- Data Map: 44 passed
- Document Type: 89 passed
- Rules: 67 passed
- Transport Profile: 41 passed
- BizFlow: 37 passed
- Cross-phase completion/HITL/transition: 55 passed

Full suite:
- 1,249 passed
- 1 skipped
- 0 failed

The one skipped test is the repository's existing intentional skip.

## Live-environment boundary
Local and mocked tests cannot prove Dell tenant SSO, current tenant-specific DOM/DDS changes, Dell AIA availability, or actual production mutations. Those remain live-environment validation items. V243R7 is designed to keep the browser/session available when such a live-only condition needs operator assistance rather than ending the run prematurely.
