# Document Type KB Overlay Fix Verdict

## Verdict

Fixed.

The latest failure was not a Data Map issue and not an inventory-limit issue. The Document Type learner completed the full UI row action phase for 562 discovered Document Types, then failed at the final `+ Add` step because a Dell DDS loading overlay intercepted pointer events for the Add button.

## Evidence from latest run log

- UI row action learning reached `562/562`.
- ID learning completed with `documentTypeIds=324/562`.
- Failure occurred only after `find_add_button` and during `click_add_button`.
- Playwright error: `Locator.click: Timeout 45000ms exceeded` because `.dds__loading-indicator__overlay` intercepted pointer events.

## Fix implemented

### 1. Overlay-aware wait added

Added `BrowserSession.wait_for_blocking_overlays_gone()` to detect active Dell/Angular loading overlays using computed style, bounding box, `aria-hidden`, and pointer-events.

It specifically handles:

- `app-loadingindicator .dds__loading-indicator__overlay`
- `.dds__loading-indicator__overlay`
- DDS loading indicators
- `role=progressbar`
- `aria-busy=true`

### 2. Generic click retry hardened

`BrowserSession.click_and_wait()` now:

- waits for blocking overlays before clicking,
- retries once when Playwright reports overlay/pointer interception,
- preserves action logging and screenshots on failure.

### 3. Document Type `+ Add` recovery added

The Document Type KB flow now uses `_click_add_doctype_with_overlay_recovery()` for the final Add-form capture step.

Recovery order:

1. Normal click after overlay wait.
2. Longer overlay wait and retry.
3. Read-only DOM fallback if the loading overlay is stale.

The fallback only opens the Add form for KB learning. It does not click Save, Create, Submit, Update, Delete, Enable, Disable, or any other mutation action.

### 4. No crash on Add-form blockage

If the Add form still cannot open, the run is marked `partial_success` and still writes the old Document Type inventory/API/UI-row KB instead of crashing after a long run.

### 5. Prevents wrong form capture

If `+ Add` does not open, the learner no longer incorrectly captures listing-page controls as Add-form controls. It records empty form controls and a warning instead.

## Validation

```text
119 passed
```

## Correct full run command

```powershell
python -m hip_id_agent.cli discover-doctype-kb `
  --config .\config.yaml `
  --customer DOCTYPE-KB `
  --input-json .\examples\uhaul_doctype_dummy_input.json `
  --known-document-type-id 10483 `
  --crawl-old-doctypes `
  --max-api-pages 250
```

Do not pass `--max-detail-rows` unless you intentionally want to limit the number of Document Types learned.
