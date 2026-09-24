# Runtime Fix After UHAUL-POASN-FULL-DUMMY-20260709-192111

## Verdict
The 192111 run shows the actual portal filling is now correct for Data Map, Source Document Type, Target Document Type, Rule, and BizFlow. BizFlow reached the wizard and passed with 55 field steps. Rule repeatable conditions also pass with 22 field steps.

The remaining failures were false negatives for Source Transport Profile and Target Transport Profile: both phases filled their required SFTP HAFT fields, and the final PNG screenshots existed in the run folder, but the phase verifier did not attach them before running strict replication.

## Fixes implemented

1. **Transport Profile screenshot capture stabilization**
   - Added delayed file-flush polling after Playwright/MCP screenshots.
   - Always records the intended golden-truth screenshot path in `files.after_fill_screenshot_png` and `files.screenshot_png`.
   - Adds a top-level `screenshots` field to the Transport Profile KB summary.

2. **Full E2E verification screenshot recovery**
   - `_locate_phase_screenshots()` now waits briefly and scans for delayed PNGs.
   - The relaxed fallback scan also retries to handle Windows/OneDrive delayed writes.
   - This prevents Source/Target TP from failing only because the screenshot was not attached in time.

3. **Validated against 192111 evidence**
   - Patched verifier result on the provided run:
     - `source_transport_profile: pass, screenshots=1`
     - `target_transport_profile: pass, screenshots=1`
     - `biz_flow: pass, screenshots=1`

## Test result
`pytest -q` => `197 passed in 18.31s`
