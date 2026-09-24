# Persistent Session Hardening from Old Multi-SSO Run

Date: 2026-07-16

## Goal

Use one authenticated Chrome context for the complete HIP no-save workflow while preventing state, overlay, page, event-log, and MCP drift from leaking across phases.

## Evidence from run 225815

The old multi-session run exposed problems that can also affect a reused session unless transitions are hardened:

- Source Document Type performed a second navigation although the browser was already on the exact target URL.
- That redundant navigation consumed about 287.7 seconds and failed through MCP navigation, `domcontentloaded`, `commit`, and `location.assign` fallbacks.
- 53 network requests were aborted during the navigation churn.
- 51 MCP actions were intercepted by a loading overlay.
- 18 actions attempted native `<select>` operations against DDS text-based comboboxes.
- 2 actions used stale or ambiguous locators.
- Four HTTP 403 responses belonged only to `/bff/activities/notifications`; they were not reliable evidence of HIP session expiry.

Data Map itself passed. The blocking behavior was session/navigation orchestration, not the Data Map form.

## Implemented architecture

### One browser and one authenticated context

The full runner starts Chrome once, attaches both MCPs once, completes SSO once when needed, and lends the same browser context to all phases.

### Phase boundary isolation without browser restart

Before each borrowed phase the runtime:

1. Closes transient dropdowns and popovers with bounded Escape actions.
2. Safely cancels an unfinished Create/Add drawer from the previous phase.
3. Handles only explicit unsaved/discard confirmation dialogs.
4. Flushes previous phase evidence.
5. Resets in-memory evidence cursors and buffers.
6. Rebinds evidence output paths to the new phase.
7. Preserves cookies, local storage, browser context, tabs, and MCP attachments.

No Save/Create/Submit action is introduced.

### Same-target navigation suppression

When the requested HIP route is already active and usable, navigation is skipped. After an MCP navigation request, the runtime waits for route commitment and the expected HIP surface before attempting any fallback. It does not reload an already-committed target URL.

### Page and tab consolidation

The runtime selects the authoritative HIP page and closes only safe stale tabs such as `about:blank` and obsolete SSO tabs after authentication. If the active page is lost, it recovers a usable page within the same context instead of starting another browser.

### Overlay recovery

Normal overlays remain blocking. A generic HIP loading overlay can be made non-intercepting only when all of the following are true:

- It has remained stale for at least ten seconds.
- The expected target surface is already proven.
- It is not a modal, warning, error, or confirmation overlay.

A diagnostic JSON artifact is written for every recovery.

### DDS control-type protection

The agent now inspects the live tag before using native select behavior. `select_option` is used only for a real `<select>`. DDS input-based comboboxes go directly through the text/option interaction path.

### Mid-run SSO expiry

The runtime distinguishes real SSO surfaces from unrelated API 403 responses. On genuine expiry it:

1. Reauthenticates in the same Chrome context.
2. Replays only the interrupted phase once from `input.json`.
3. Writes `phase_execution_attempts.json`.
4. Fails closed if the replay still fails.

Non-authentication errors are never retried as SSO.

## New evidence

- `browser_session_reuse_contract.json`
- `browser_session_manifest.json`
- `browser_session_final_state.json`
- `<phase>/browser_session_reuse.json`
- `<phase>/phase_boundary_cleanup.json`
- `<phase>/phase_execution_attempts.json`
- `session_page_consolidation.json`
- `stale_overlay_recovery_*.json`

## Expected complete-run state

For seven reached phases under a valid session:

```json
{
  "start_count": 1,
  "borrow_count": 7,
  "sso_prompt_count": 0,
  "reauth_count": 0,
  "phase_transition_count": 7,
  "single_persistent_context": true
}
```

`sso_prompt_count` may be 1 for the initial login. If the Dell session genuinely expires, a valid recovery can produce `sso_prompt_count: 2` and `reauth_count: 1`.

## Safety

- Save/Create/Submit/Delete/Deploy remain blocked.
- A notification-service 403 cannot by itself trigger SSO recovery.
- Modal, warning, error, and confirmation overlays are not suppressed.
- Only the interrupted phase may replay, and only once.

## Verification

- Python compilation passed.
- All 309 tests passed in isolated batches.
- No authenticated HIP live rerun was possible in this environment.
