# Changed Files — Autonomous Web Agent Mission Controller (2026-07-21)

## New

- `hip_id_agent/mission_controller.py` — crash-safe mission ledger, fail-closed
  resume adoption, resumable-run discovery, per-run entity registry, final
  mission completion verdict.
- `tests/test_autonomous_mission_until_complete.py` — 14 regression tests.
- `AUTONOMOUS_WEB_AGENT_MISSION_CONTROLLER_FIX_20260721.md`
- `LIVE_TESTING_README_AUTONOMOUS_MISSION_20260721.md`

## Modified

- `hip_id_agent/runtime_self_heal.py` — `browser_disconnected` classification;
  `restart_browser_session` safe action with ladder
  `restart_browser_session → recover_page_and_route`.
- `hip_id_agent/browser_session.py` — `BrowserSession.restart()` persistent
  context relaunch after crash/disconnect.
- `hip_id_agent/dummy_fill_e2e.py` — mission controller integration, resume
  options, adopted-phase skip, per-phase judged evidence persistence, entity
  forward-injection, mission verdict in the aggregate.
- `hip_id_agent/cli.py` — `--resume-run` / `--auto-resume` on
  `run-full-dummy-fill` with validation.
- `RUN_ALL_PHASES_UNTIL_COMPLETE.ps1` — `-Resume` / `-ResumeRunDir`.
- `CHANGELOG.md`
