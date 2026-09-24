# HIP Portal Agent V234 — Implementation and Certification

Release: **2.3.4**  
Build date: **2026-09-14**

## Implemented

- Browser-Use-style multi-color live browser visualization tied to the governed semantic action gate:
  - blue: visible controls the agent can see
  - violet: ranked semantic candidates
  - yellow: identified/selected target
  - orange: final pre-dispatch acting/fill target
  - green: independently verified result
  - teal: newly revealed controls
  - red: failed verification
  - pink: authoritative foreground drawer/dialog/surface
- Overlays use `pointer-events:none` and cannot intercept browser input.
- Foreground ownership filtering prevents background controls beneath a drawer/dialog from becoming actionable visual candidates; detached owned listboxes/menus are allowed only when visible/topmost.
- Overlay selectors and geometry are runtime-only and are scrubbed from persisted Live View evidence.
- Agent Live View records selected → planner aligned → acting → verified/failed, with screenshots of the actual browser overlay state.
- Control Center adds visual legend, action-state banner, overlay counters, adaptive-learning score, stale/drift/fresh counts, and recent observable decision history.
- Website world model adds confidence half-life decay, stale-age detection, repeated-contradiction drift detection, automatic demotion, and success-based recovery.
- Candidate ranking and transition planning use effective (freshness-aware) confidence rather than historical confidence alone.
- Governing rule: **MEMORY PROPOSES; LIVE PAGE AUTHORIZES**.
- Existing seven-phase HIP form flow, dynamic dropdown resolution, same-page/in-page form ownership, PyAutoGUI MCP primary path, Playwright fallback, API/runtime governance, and BizFlow lifecycle remain intact.
- SFTP-HAFT role defaults remain:
  - Sender/Dell/source: `da-sender-sftphaft-dce-shared`
  - Partner/Receiver/target: `pt-receiver-sftphaft-dce-shared`

## Certification performed

- Python compile: 112 project Python files compiled successfully.
- JavaScript syntax: `webui/app.js` passed `node --check`.
- V234 + V233 Stage 2/3/4 focused regression: 39 passed, 0 failed.
- Complete repository collection: 1,164 tests.
- Complete non-overlapping suite result: **1,163 passed, 1 skipped, 0 failed**.
- Seven-phase local Chromium final-mission UAT: **PASS**, including Data Map, both Document Types, Rule, both Transport Profiles, BizFlow, and BizFlow Edit/Save/Validate/Deploy.
- Local UAT explicitly reports `dell_environment_contacted: false`; it is a local browser certification, not a claim of a live Dell tenant run.

## Safety / persistence contract

No customer-entered values, CSS/XPath selectors, generated DOM ids, screen coordinates, viewport coordinates, or bounding boxes are intentionally persisted as adaptive semantic knowledge. Memory is advisory and must be re-proven against the current live page before execution.
