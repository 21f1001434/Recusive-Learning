# V243R25: the backend stays online, and the Windows certification shows its real result (2026-09-28)

## What was reported

- The Control Center kept switching to **"Backend offline"**.
- **Certify Windows runtime** ended as **NO_GO** with 1 blocker, no certificate, no expiry and "No rows available".

## What was really happening

That NO_GO was **not a certification result**. When the request fails, the Control Center fills in `{decision: NO_GO, blocker_count: 1, checks: []}` itself. Here the request failed for two reasons, and the backend went "offline" for a third.

| # | Cause | Evidence |
|---|---|---|
| 1 | The certification ran **inside the backend**. The certification opens the headed browser, waits for Dell SSO, starts the MCP servers and calls Dell AIA with blocking calls. Since R24 it also asks every model the qualification questions. All of this froze the backend's event loop, so every 3-second status poll hung, and the page showed "Backend offline". | Code: `/api/mission/live-runtime-certification` awaited `certify_live_runtime` in the backend process. `_probe_text_model` and the model qualification are synchronous. |
| 2 | The **Bun proxy** (`webui/server.js`, used by `bun run platform`) gave up on the backend after about 300 s and answered **503 "HIP backend is unavailable"**. A certification waiting for SSO takes longer than that. | Measured through the real `server.js` against a backend that answers slowly: a 130 s request came back 200; a 320 s request came back **503 after 288 s**. |
| 3 | The status polls were expensive and stacked up. On every poll the backend: re-read the whole mission log for its last 30 KB; started a `tasklist` process per process check on Windows; built the model portfolio three times; and parsed the whole model-usage ledger twice. A new poll started every 3 s even if the last one had not answered. One failing manifest made `/api/runtime/status` answer 500, which the page also shows as "Backend offline". | Code review; profile of `runtime_status`. |

## What changed

**The certification runs in its own process.**
- **Certify Windows runtime** now starts `python -m hip_id_agent.cli certify-live-runtime` as a child process and returns at once. The backend is never blocked, and a browser or MCP crash cannot take it down.
- The child writes every finished check to a progress file and the certificate (or its error) to a result file:
  - `certify-live-runtime --progress-json … --result-json …`;
  - `GET /api/mission/live-runtime-certification/status` returns `running` / `done` / `failed`, with the checks so far, the certificate, or the error and the end of its log;
  - `POST /api/mission/live-runtime-certification/stop` stops it.
- Only one runs at a time: a second click follows the running one.
- A certification that has not finished within `live_runtime_certification.job_timeout_seconds` (30 min) is stopped and reported.
- The Control Center follows it while it runs: "Running for N s · last check: …", and the table fills in check by check. At the end it shows:
  - **GO** / **NO_GO** with every check and its reason (a failing check now always carries its probe's error, for example "AIA endpoint/token missing");
  - **"Not completed"** with the real error and the log, if it could not finish.
  It never shows an invented NO_GO. Reloading the page picks the running certification up again.
- Live GO/NO-GO's automatic renewal of an expired certificate uses the same child process. Its Dell AIA text test and file checks run off the event loop.

**The proxy never cuts a request short.**
- `server.js` calls the backend with `timeout: false` and `idleTimeout: 255`.
- If a timeout ever happens, it says so (504, "did not answer in time") instead of "backend unavailable".
- `platform.js` restarts a backend it started if that backend ever exits.

**Cheap, safe status polls.**
- The mission log is read from its end.
- The Windows process check asks the kernel (`OpenProcess` / `GetExitCodeProcess`) instead of starting `tasklist`.
- The model portfolio, replay policy and skill library are built once per poll.
- The usage ledger is read from its end.
- Overlapping polls share one computation.
- An error in one manifest gives a *partial* status (`degraded`, `status_error`), never a 500.

**The page polls calmly.**
- One status poll at a time, each with a 20 s limit.
- A single slow answer shows **"Backend busy"**. **"Backend offline"** appears only when the backend is unreachable or three polls in a row failed.
- Live GO/NO-GO that cannot complete says "NOT COMPLETED" with the reason, not NO_GO with an invented blocker.

## Verification

| Check | Result |
|---|---|
| End to end in this sandbox: real uvicorn backend, the real Bun `server.js` in front of it, a real `certify-live-runtime` child process | POST answered in 0.11 s. While the certification ran, `/api/runtime/status` answered in 0.16–0.9 s and `/api/discovery/status` at once. The result table showed each check with its reason. It was NO_GO here only because this sandbox is not the Windows workstation: no Windows desktop, no Dell AIA credentials, no display for the headed browser. |
| Bun proxy with `timeout: false` against a backend that answers after 330 s | HTTP 200 after 330 s (before the fix: 503 after 288 s). |
| `tests/test_v243r25_backend_responsive_certification_job.py` | 15 passed |
| Existing certification, readiness-renewal, backend and Control Center tests (`test_v219_live_runtime_certification`, `test_v226_pyautogui_auto_cert_recovery`, `test_hip_platform_backend`, `test_javascript_ui_control_center` …) | passed |
| Full suite (200 files) | 1,480 passed, 1 skipped. Two cases apply only outside this environment: `test_streamlit_preflight_passes_current_package_and_blocks_missing_golden` needs the gitignored `uploads/*.jar`, which ships in the package; `test_v210_layer1_windows_path_guard.py` runs on Windows only. |
| 7-phase local mission UAT (`certify-final-mission`) | PASS: 7/7 phases; Edit / Save / Validate / Deploy PASS; final BizFlow status Deployed |
| `VERIFY_V243R25_INSTALL.ps1` R25 smoke checks | `R25_CERTIFICATION_IN_OWN_PROCESS_OK`, `R25_STATUS_POLLS_OK`, `R25_PROXY_AND_UI_OK` (and the R24 checks it calls first) |

## Apply

```powershell
.\APPLY_V243R25_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R25_INSTALL.ps1
```

R25 includes R13–R24. Restart the Control Center (`bun run platform`) so the new `server.js` and backend are used. Then click **Certify Windows runtime** once. The panel shows each check as it finishes; complete Dell SSO in the browser window if it asks. A remaining BLOCK row now names its real cause.
