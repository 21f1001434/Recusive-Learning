# HIP Portal v1.8.5 Final Certification — 2026-09-01

## Live defects addressed
- Document Type no-progress execution can no longer spin for hours.
- Document Type uses deterministic-first execution from the reviewed Unified Deep KB; broad pre-fill discovery is recovery-only.
- Source Document Type judge-approved structural memory is replayed for Target Document Type with fresh live semantic binding and no customer-value reuse.
- Capabilities and API Contracts are populated from canonical KB immediately and updated with runtime candidate/live observations even when a phase fails.
- AgentQ reward is operational: persistent trajectory reward/success-rate affects action ranking; repeated losing actions are suppressed; MCP advice is reward/safety-gated.
- Failure diagnosis captures a value-free semantic DOM structure plus screenshot from the existing authenticated page.

## Anti-stall policy
`until_complete` is progress-driven, not unlimited. `max_no_progress_repeats` and `max_phase_wall_seconds` remain enforced. Phase execution is wrapped in an async wall-clock timeout, so an inner portal interaction cannot monopolize Chrome indefinitely.

## Verification
- Automated regression suite: 695/695 PASS.
- Python source compile validation: 185 files PASS.
- JavaScript syntax: app.js/server.js/platform.js PASS.
- package.json/config.yaml parse PASS.
- FastAPI health PASS.
- Canonical capability graph present: 80 capabilities / 18 unique API contracts.
- Source Document Type preflight PASS.
- Target Document Type preflight PASS.
- Source+Target Document Type preflight PASS.
- Transport Profile preflight PASS.
- Full seven-phase preflight PASS.
- v1.8.5 wheel build PASS using local no-build-isolation packaging.

## External boundary
Dell SSO, the current live HIP tenant DOM/DDS behavior, Windows DPI/desktop focus for PyAutoGUI, and tenant-side service/API availability cannot be exercised from the Linux release sandbox. Live execution is fail-closed and now has bounded stall evidence for these environment-dependent failures.
