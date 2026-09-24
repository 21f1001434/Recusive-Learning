# Persistent Session Hardening Rerun Guide

## Package use

Use the complete package as the new baseline. Preserve your existing:

- `.env`
- `config.yaml`
- `uploads/`
- `golden_screenshots/`
- `data/hip_memory/portal_brain/`

## Run

Use the same `run-full-dummy-fill` command. No new CLI flag is required.

## During execution

1. Complete Dell SSO only when the browser presents the actual Dell identity-provider page.
2. Do not close the shared Chrome window.
3. Do not manually open additional HIP tabs.
4. The runner will move through all phases in the same context.

## Verify single-session behavior

Inspect the newest run:

```powershell
$LATEST_RUN = Get-ChildItem $RUNS_DIR -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content "$($LATEST_RUN.FullName)\browser_session_final_state.json"
```

Normal expectations:

- `start_count = 1`
- `single_persistent_context = true`
- `sso_prompt_count = 0` when an authenticated profile was reused, otherwise `1`
- `reauth_count = 0`
- `borrow_count` equals the number of reached phases

List phase reuse artifacts:

```powershell
Get-ChildItem $LATEST_RUN.FullName -Recurse -Filter browser_session_reuse.json |
  Select-Object FullName
```

Check phase-transition cleanup:

```powershell
Get-ChildItem $LATEST_RUN.FullName -Recurse -Filter phase_boundary_cleanup.json |
  Select-Object FullName
```

Check genuine reauthentication/replay:

```powershell
Get-ChildItem $LATEST_RUN.FullName -Recurse -Filter phase_execution_attempts.json |
  Select-Object FullName
```

A phase should have two attempts only when the first one ended with `HIP_AUTH_SESSION_EXPIRED`.

## Expected improvements over run 225815

- No 287-second navigation retry when the target route is already active.
- No browser restart between phases.
- No repeated initial SSO prompt for each form.
- No native-select probe on DDS text comboboxes.
- Previous phase drawers and dropdowns are cleaned before the next route.
- Evidence files remain phase-local even though the browser is shared.
- Stale generic loading overlays cannot intercept actions indefinitely.

## Share after rerun

Zip the complete newest run directory, including `browser_session_final_state.json`, phase reports, DOM events, MCP action logs, and section judges.
