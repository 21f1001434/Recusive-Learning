# v2.1.3 Live Witness / UAT Gate

## Purpose

This is the final pre-UAT layer. It validates the integrated agent against the real HIP tenant without permitting portal/API mutation. It is designed to answer one question before business testing: **can the real AutoWebGLM + Playwright MCP agent traverse and verify the selected HIP workflow in one Dell SSO session without stalling or changing backend state?**

## Execution contract

1. Live GO/NO-GO receipt is required and is fingerprint-bound to the `live_witness` execution profile.
2. Edge is preferred; Chrome/Playwright Chromium are startup-only fallbacks before SSO. The selected browser is locked after authentication.
3. AutoWebGLM is the primary planner.
4. Official Playwright MCP is the primary physical executor.
5. Deterministic Python Playwright is the exact DDS fallback only.
6. Chrome DevTools MCP independently observes DOM/network/console state.
7. Dell AIA text and vision judges remain required according to the mission profile.
8. P01-DM -> P07-BF transitions use the all-phase coordinator and no-progress watchdog.

## Hard safety boundary

Witness mode is applied **after** the normal autonomous/AgentQ profile so no earlier profile can re-enable submit capture. It forces:

- `capture_submit_api = false`
- `api_mode = capture`
- `allow_api_mutation = false`
- mutation-control clicks prohibited
- dynamic `+ Add` / repeatable-row openers allowed

The backend rejects witness+write/mutation combinations even if a client bypasses the JavaScript UI.

## Independent witness certificate

`live_witness_certificate.json` is written before the final mission verdict. It inspects structural action labels and redacted endpoint templates only. It stores no credentials, cookies, request bodies or entered values.

Witness PASS requires:

- selected mission phases completed;
- terminal transition gate passed;
- zero Create/Save/Submit/Finish/Deploy/Delete/Remove/Publish/Update/Migrate/Clone control clicks;
- zero observed mutation-classified API requests.

Read/search/validation traffic remains allowed.

## First Dell test

Use a short local path such as `C:\HIP\v213`. In the Control Center:

1. Keep **Live witness mode** checked.
2. Run static preflight.
3. Test text model.
4. Test vision model.
5. Run Live GO/NO-GO.
6. Start live witness.
7. Complete Dell SSO once.
8. Watch the P01-P07 mission trace.
9. Review `live_witness_certificate.json` and `mission_completion_report.json`.

Only after the witness passes should the operator intentionally disable witness mode for a governed execution test.
