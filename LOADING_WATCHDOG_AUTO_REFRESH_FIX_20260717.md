# HIP Portal Loading Watchdog and Safe Auto-Refresh Fix

## Objective

The agent must wait for real Dell HIP Angular/DDS loading to complete. When the same blocking loading state remains continuously visible for more than 120 seconds, the agent must refresh the current page once, preserve the authenticated Chrome context, reconstruct the current phase, and continue through the existing ReAct and judge pipeline.

## Previous behavior

The runtime used several short loading waits. A generic stale overlay could also have its pointer-events disabled after roughly ten seconds. This created two risks:

1. A real portal load could be bypassed before its data and conditional form controls were ready.
2. A genuinely stuck page could remain blocked without the explicit two-minute refresh requested by the user.

## Implemented architecture

### Continuous loading timer

The timer is keyed by phase and active HIP URL. Repeated short action waits cannot reset it.

Default policy:

```yaml
portal:
  loading_watchdog_timeout_seconds: 120
  loading_watchdog_poll_seconds: 1.0
  loading_watchdog_max_refreshes_per_phase: 1
```

### Loading recognition

The watchdog observes visible Angular/DDS loading surfaces including:

- Dell loading-indicator overlays
- DDS spinners
- `role=progressbar`
- `aria-busy=true`
- visible loading/spinner/progress classes

It records a stable loading fingerprint, current URL, DOM readiness, overlay metadata, console evidence, network evidence, Playwright MCP evidence, Chrome DevTools MCP URL, and screenshot.

### Two-minute recovery

When loading remains active for 120 seconds:

1. Capture pre-refresh evidence.
2. Refresh the active page using Python Playwright inside the existing persistent browser context.
3. Preserve cookies, local storage, SSO state, tabs and MCP attachments.
4. Reinstall click and DOM mutation observers.
5. Verify both MCP tools are still observing the same browser surface.
6. Wait again for loading to complete.
7. Resume the same phase from its exact `input.json` branch.

If loading remains active after the allowed refresh, execution fails closed and enters the bounded runtime self-heal classifier.

### No premature overlay bypass

The runtime no longer sets `pointer-events:none` on a loading overlay. It waits for completion or performs the two-minute refresh.

### Self-heal integration

The new failure signature:

```text
HIP_PORTAL_LOADING_TIMEOUT_AFTER_REFRESH
```

is classified as `blocking_overlay`. The safe recovery ladder starts with:

```text
refresh_page_and_reopen
```

The repair remains candidate knowledge until the complete phase passes deterministic, text and vision judges.

## Evidence files

Each loading event writes evidence under:

```text
<phase>/mcp_runtime/loading_watchdog/
```

Typical files:

```text
loading_watchdog_0001_timeout_before_refresh.json
loading_watchdog_0001_timeout_before_refresh.png
page_refresh_0001.json
loading_watchdog_0002_completed.json
```

## Safety boundaries

- No Save, Create, Submit, Delete, Deploy, Publish or Update action is introduced.
- The page refresh preserves the same authenticated persistent context.
- One automatic refresh is allowed per phase and URL by default.
- A persistent load after refresh remains blocking.
- Real SSO redirects are classified separately and invoke the existing reauthentication path.

## Verification

- Python compilation: passed
- Targeted loading-watchdog tests: 5 passed
- Session/ReAct/self-heal regression set: 29 passed
- Complete automated suite: 324/324 passed
- Premature pointer-event overlay bypass removed
