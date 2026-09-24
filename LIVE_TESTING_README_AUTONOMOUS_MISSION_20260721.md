# Live Testing: Autonomous Mission — All Phases Until Complete + Resume

## Preserve existing learning

```powershell
Copy-Item -Recurse -Force .\data\hip_memory\portal_brain C:\hip_backup\portal_brain
```

Restore it after extracting the new package to `data\hip_memory\portal_brain`.

## Verify the regression suite first

```powershell
python -m pytest tests -q
```

Expected: all tests pass, including the 14 new
`tests/test_autonomous_mission_until_complete.py` tests.

## Fresh autonomous run

```powershell
.\RUN_ALL_PHASES_UNTIL_COMPLETE.ps1 -RunsDir "C:\hip_runs"
```

## Resume an interrupted run

If a previous until-complete run was stopped (Ctrl+C, crash, browser death,
reboot), do NOT replay the completed phases. Either let the agent find the
newest resumable run:

```powershell
.\RUN_ALL_PHASES_UNTIL_COMPLETE.ps1 -RunsDir "C:\hip_runs" -Resume
```

or name the exact prior run directory:

```powershell
.\RUN_ALL_PHASES_UNTIL_COMPLETE.ps1 -RunsDir "C:\hip_runs" -ResumeRunDir "C:\hip_runs\<prior-run-id>"
```

Adoption is fail-closed. A prior phase is adopted only when it holds all of:

```text
<phase>\phase_exact_state_lock.json      (exact completion checkpoint pass)
<phase>\section_judge_gate.json          (pass: true)
<phase>\phase_verification.json          (persisted judged verification)
```

and no unresolved `section_judge_block_diagnosis.json`. Every other phase
re-executes live in the shared authenticated Chrome context.

## What to inspect after the run

```text
mission_state.json                      per-phase authoritative status ledger
mission_resume_manifest.json            what was adopted and what was skipped
mission_completion_report.json          application_complete true/false
mission_completion_report.md            human-readable mission table
mission_entity_registry.json            per-run entity names (value-scoped, never promoted to the brain)
<phase>\resumed_from_prior_run.json     present only for adopted phases
<phase>\phase_verification.json         persisted judged verification (new every judged pass)
<phase>\phase_judge_result.json         persisted judge outcome
```

## Browser-death recovery

If Chrome dies or disconnects mid-phase, the self-heal loop now classifies it
as `browser_disconnected` and executes `restart_browser_session`: the same
persistent user-data-dir is relaunched, existing Dell SSO cookies are reused
(an expired session falls back to the normal SSO prompt), MCP attachments are
recycled, and the interrupted phase replays through the normal deterministic
runtime. Inspect:

```text
runtime_self_heal\<phase>\attempt_*\self_heal_decision.json   (action: restart_browser_session)
browser_session_final_state.json                              (start_count > 1)
```

## Completion definition

The application is complete only when `mission_completion_report.json` shows
`"application_complete": true`: every selected phase passed the deterministic
exact-state verification and the independent text + vision judges, either in
this run or adopted fail-closed from the resumed run.
