# Rerun Guide — Startup SSO Preflight Fix

Use the same `run-full-dummy-fill` command and existing configuration.

Expected startup sequence:

1. Browser opens once.
2. Data Map preflight starts from `about:blank` or the existing tab.
3. Dell SSO appears.
4. Console asks the user to complete SSO.
5. After login, the same attempt resumes and verifies Data Maps through Python Playwright, Playwright MCP and Chrome DevTools MCP.
6. The remaining phases reuse the authenticated session.

Expected evidence:

- `data_map/phase_attempt_01_agentic_preflight.json`
- `mcp_runtime/navigation_react_trace.json`
- `mcp_runtime/sso_completion_gate.json`
- `browser_session_manifest.json`

The preflight JSON should contain:

```json
{
  "status": "ready",
  "pass": true,
  "route": {
    "status": "authenticated_after_sso",
    "pass": true
  }
}
```
