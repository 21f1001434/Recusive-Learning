# Loading Watchdog Rerun Guide

Use the same full dummy-fill command. No new CLI flag is required.

## Expected runtime behavior

For ordinary loading:

```text
Loading detected
→ wait until the loader disappears
→ continue the current action
```

For a stuck loader:

```text
Loading detected
→ same loader remains for 120 seconds
→ capture MCP/DOM/network/screenshot evidence
→ refresh current page once
→ preserve SSO and browser context
→ restore observers
→ wait for readiness
→ replay only the interrupted phase
→ require deterministic/text/vision judge pass
```

## Expected evidence

Check the current phase directory:

```powershell
Get-ChildItem "$($LATEST_RUN.FullName)" -Recurse |
  Where-Object { $_.FullName -match "loading_watchdog" } |
  Select-Object FullName
```

A successful recovery should include:

```text
loading_watchdog_*_timeout_before_refresh.json
page_refresh_*.json
loading_watchdog_*_completed.json
```

The page refresh audit should contain:

```json
{
  "status": "refreshed",
  "same_persistent_context": true,
  "dual_mcp_observation": "pass"
}
```

## Failure behavior

When loading remains active after the permitted refresh, the phase stops with:

```text
HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH
```

The runtime self-heal report will classify it as `blocking_overlay`, capture the evidence, and apply only bounded safe recovery actions.
