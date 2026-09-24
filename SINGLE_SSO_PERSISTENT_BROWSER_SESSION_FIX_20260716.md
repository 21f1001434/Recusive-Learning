# HIP Portal Single-SSO Persistent Browser Session Fix

## Problem

The full dummy-fill orchestrator ran each phase inside its own `BrowserSession` context:

- Data Map opened Chrome and completed SSO.
- Source Document Type created another browser context and requested SSO again.
- Target Document Type, Rule, Transport Profiles and BizFlow repeated the same lifecycle.

Although the Chrome profile directory was persistent, repeatedly closing and relaunching the browser/context also restarted MCP attachments and could lose transient corporate authentication, tabs, local storage, Angular state and in-memory evidence.

## Correct architecture

A full run now creates exactly one `BrowserSession` at the orchestrator level. The same session is borrowed by every phase:

```text
Start one persistent Chrome context
  -> attach Playwright MCP once
  -> attach Chrome DevTools MCP once
  -> Data Map requests SSO only when unauthenticated
  -> Source Document Type reuses authenticated session
  -> Target Document Type reuses authenticated session
  -> Rule reuses authenticated session
  -> Source Transport Profile reuses authenticated session
  -> Target Transport Profile reuses authenticated session
  -> BizFlow reuses authenticated session
  -> close browser once after the phase loop
```

SSO is requested again only if navigation proves that the live session has expired or returned to an identity-provider page.

## Implementation

### Shared orchestration session

`FullDummyFillE2EFlow.run()` now:

1. Creates one `BrowserSession` under the root run directory.
2. Starts it once.
3. Passes it to every phase as `browser_session=shared_browser`.
4. Closes it once in a `finally` block.

### Borrowed browser scope

`browser_session_scope()` supports two modes:

- Standalone phase command: creates and owns a browser as before.
- Full E2E run: borrows the existing browser and does not close it.

Therefore standalone commands remain backward compatible.

### Authentication state

The session records:

- `session_id`
- browser start count
- phase borrow count
- SSO prompt count
- whether authentication succeeded
- phase history
- current HIP URL

A valid authenticated page is reused silently. The manual SSO message appears only when the current live page is an SSO/login surface.

### MCP continuity

Both MCP backends remain attached to the same Chrome instance for the full run. They are not restarted between phases.

### Evidence continuity

Registering a borrowed phase re-enables log flushing so later-phase DOM events, mutations, actions and network evidence are written even though an earlier phase already flushed the same shared session.

## New evidence files

Root run directory:

- `browser_session_reuse_contract.json`
- `browser_session_manifest.json`
- `browser_session_final_state.json`

Each phase directory:

- `browser_session_reuse.json`

Expected final values for a complete seven-phase run:

```json
{
  "start_count": 1,
  "borrow_count": 7,
  "sso_prompt_count": 1,
  "authenticated_once": true,
  "single_persistent_context": true
}
```

`borrow_count` can be lower when a strict judge blocks the run before later phases. `sso_prompt_count` can be `0` when the persistent Chrome profile is already authenticated, or greater than `1` only when the real corporate session expires during the run.

## Safety

The change does not weaken the no-save policy. Save/Create/Submit/Delete/Deploy and other mutating actions remain blocked.

## Verification

- Python compilation: passed
- New session-reuse tests: passed
- Existing SSO redirect and navigation tests: passed
- Complete test suite: 305 passed
