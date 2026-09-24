# v2.1.2 Live GO/NO-GO Readiness Gate

This layer closes the gap between configuration-level preflight and actual runtime availability.

## Blocking checks

1. Selected input/phase preflight.
2. Expert-skill vetting.
3. Microsoft AutoGen AgentChat 0.7.5 imports.
4. AutoWebGLM enabled as the primary decision framework.
5. Playwright MCP configured as the primary safe-action executor.
6. A supported browser can actually launch headlessly: Edge first, Chrome second, Playwright Chromium third.
7. Official Playwright MCP can start and publishes navigate, snapshot, click, type, select-option and screenshot tools.
8. Chrome DevTools MCP can start and publishes DOM snapshot, network and console tools.
9. Dell AIA text deployment returns the synthetic HIP_TEXT_OK marker.
10. Dell AIA vision deployment proves real image understanding through the synthetic red/blue image probe.
11. The selected runs/evidence path is writable using the same safe-I/O layer as a mission.
12. No other mission controller process is running.

## Receipt

A passing run issues a short-lived opaque token stored both in the JavaScript state and a server-side receipt. The receipt is bound to config, input JSON, runs path, golden/upload paths, selected phases and API mode. Mission start independently recomputes that fingerprint and fails with HTTP 412 on missing, expired, mismatched or stale readiness.

## SSO boundary

The live gate deliberately does not fabricate Dell SSO success. It proves the browser/MCP/model/runtime stack before portal launch. Once the chosen browser opens, the existing single-session SSO preflight validates authentication and locks that browser for the mission.
