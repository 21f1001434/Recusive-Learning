# V228 Build Verification — PyAutoGUI MCP Primary Browser Interaction

Date: 2026-09-10
Version: 2.2.8

## Implemented

- PyAutoGUI MCP promoted from tertiary fallback to **primary physical interaction** for:
  - click
  - form fill
  - search fill
  - key press/hotkey
- Playwright MCP retained as:
  - semantic/accessibility evidence
  - deterministic fallback
  - exact post-action/value verifier
  - governed URL navigation where applicable
- Python Playwright retained as final compatibility fallback.
- Chrome DevTools MCP retained as independent DOM/network/console witness.
- All HIP create sections use the same-page/in-page `+ Add` contract:
  - Data Maps
  - Document Types
  - Rules
  - Transport Profiles
  - BizFlow
- BizFlow retains the extra same-page template/card -> tabs step.
- Added high-confidence visual structural recovery:
  - Browser-Use state context + Gemma vision locate a missing-but-visible structural control.
  - PyAutoGUI MCP clicks normalized viewport coordinates.
  - trusted click/effect evidence is required.
  - visual-coordinate Save/Create/Delete/Deploy is blocked.
- Exact-DOM final mutations may use PyAutoGUI primary only after the normal mutation/semantic policy gates.
- Mutation replay protection preserved: an uncertain physical final mutation is never replayed through a fallback.
- Added optional isolated `browser-use/web-ui` sidecar runner. The generic WebUI is not a competing HIP mutation executor.
- Added deterministic local HIP SPA browser fixture covering all five create sections.

## Validation

The full suite contains **1,090 tests**. It was executed in three non-overlapping batches to avoid the harness wall-clock cap:

- Batch 1: 880 passed
- Batch 2: 112 passed
- Batch 3: 98 passed
- Total: **1,090 / 1,090 passed**

Additional checks:

- `python -m compileall -q hip_id_agent backend` — PASS
- `config.yaml` parse — PASS (`interaction_mode=primary`, PyAutoGUI MCP required, mutation clicks governed/enabled)
- `config.example.yaml` parse — PASS
- `config.mcp-required.windows.yaml` parse — PASS
- Real Chromium local mock — PASS for Data Maps, Document Types, Rules, Transport Profiles and BizFlow in-page Add/form flow plus PyAutoGUI-MCP-style coordinate click and typing.

## Environment boundary

This build was not executed against the private Dell HIP environment. The local browser test validates the browser-automation architecture, coordinate conversion, interaction ordering, same-page form behavior, and verification contracts.

A real PyAutoGUI MCP certification must be run on the user's visible Windows workstation because PyAutoGUI operates on the current desktop/display and requires a real GUI session. The existing Dell SSO/live certification flow remains the final workstation gate.
