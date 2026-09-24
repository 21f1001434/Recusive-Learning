# HIP Portal Agent V235 / v2.3.5 — Live Failure Fix

Date: **2026-09-17**

## What failed in the Dell live run

The failure bundle supplied after V234 exposed two independent runtime defects.

1. **Exact navigation target was not guaranteed to remain in semantic inventory.**  The live action `Open Biz Flow navigation` had a vetted Playwright target, but the semantic inventory query could omit a custom/DDS container that was only marked with `data-hip-semantic-anchor`. An unrelated semantic candidate could therefore remain top-ranked and the action was blocked around confidence `0.34` by the mutation-grade `0.90` gate.
2. **Browser-Use recovery could interfere with the HIP-owned CDP/browser session.**  The live log then showed the Browser-Use/session lifecycle clearing owned targets and resetting the session, followed by `Failed to open new tab - no browser is open`, `browser not connected`, CDP WebSocket closure, `ConnectionRefusedError`, Windows error 1225, and exhausted reconnection attempts. Once the shared browser transport was gone, downstream phase/section-judge failures were consequences rather than independent form failures.

## Runtime fixes

### Exact-anchor semantic execution

- `[data-hip-semantic-anchor]` is now part of the live semantic inventory selector, even when the exact Playwright target is a custom/DDS container rather than a standard input/button.
- When the currently vetted Playwright locator produces an exact anchored candidate, that candidate is preferred over unanchored semantic lookalikes.
- Safe structural actions use a separate **exact-anchor-only** lane:
  - structural/open/navigation/expand/collapse/tab/next/continue/back/close/cancel/add-row: confidence `>= 0.52`, margin `>= 0.00`;
  - anchored non-structural interaction: confidence `>= 0.64`, margin `>= 0.02`;
  - unanchored execution remains on the original strict threshold: confidence `>= 0.90`, margin `>= 0.08`.
- The lower structural lane never applies to final/destructive mutations such as Save, Submit, Deploy, Publish, Update, Confirm, Migrate, Delete, or Remove.
- Live DOM/accessibility/foreground ownership remains authoritative. Memory cannot authorize an action on its own.

### Safe Browser-Use-style observer

- When HIP owns the Playwright browser/context, Browser-Use is no longer allowed to own or reset that browser lifecycle.
- The default managed-browser mode produces a Browser-Use-style, value-free interactive snapshot directly from the HIP Playwright page.
- Detach no longer calls public Browser-Use `stop()`, `kill()`, or other browser lifecycle shutdown operations. It only tears down bridge-local transport/task state that HIP created for observation.
- Selectors, XPath, coordinates, bounding boxes, and customer-entered values remain excluded from persistent semantic memory.

### Disconnect classification and self-heal

The recovery classifier now explicitly recognizes the real live signatures observed in the failure bundle, including:

- `Failed to open new tab - no browser is open`
- `no browser is open`
- `browser not connected`
- `reconnection attempts failed`
- `ConnectionRefusedError`
- `remote computer refused the network connection`

Those signals are classified as a browser transport/session failure rather than a form/section-judge failure, so the runtime can take the browser-session recovery ladder instead of repeatedly retrying downstream phases against a dead session.

## Configuration added

`config.yaml`, `config.example.yaml`, and `config.mcp-required.windows.yaml` now expose:

```yaml
semantic_understanding:
  anchored_execute_confidence_threshold: 0.64
  structural_opener_confidence_threshold: 0.52
  anchored_ambiguity_margin: 0.02
  prefer_vetted_locator_anchor: true

browser_use:
  safe_managed_browser_mode: true
  non_invasive_detach: true
```

## Failure-specific regression

`tests/test_v235_live_failure_20260917.py` covers the exact live incident class:

- exact semantic anchor must be inventoried;
- safe structural navigation gets only the exact-anchor structural lane;
- Save/Deploy/Submit/Migrate/Delete are excluded from that lane;
- the observed browser-disconnect messages classify as browser crashes;
- Browser-Use detach cannot call public browser `stop()`/`kill()`.

## Verification

After the patch and v2.3.5 version promotion:

- focused V235/V234/V233 regression: **51 passed, 0 failed**;
- repository: **1,170 collected — 1,169 passed, 1 skipped, 0 failed**;
- Python compilation: PASS;
- `webui/app.js` syntax: PASS;
- local seven-phase Chromium final mission: PASS;
- Data Map → Source Document Type → Target Document Type → Rule → Source TP → Target TP → BizFlow: PASS;
- BizFlow Edit → Save → Validate → Deploy: PASS;
- source SFTP-HAFT deployment group: `da-sender-sftphaft-dce-shared`;
- target SFTP-HAFT deployment group: `pt-receiver-sftphaft-dce-shared`;
- local UAT is mock-only and reports `dell_environment_contacted: false`.

## Scope statement

The defects were identified from a real Dell live-run failure bundle. The fixes are implemented and locally regression/UAT certified. This package does **not** claim that a new live Dell mutation run was performed from the certification environment; the next live run remains the authoritative test of tenant-specific DOM/CDP behavior.
