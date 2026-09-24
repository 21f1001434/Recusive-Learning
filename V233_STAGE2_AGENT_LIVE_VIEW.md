# V233 Stage 2 — Agent Live View

Stage 2 adds Browser-Use-style transparent browser-agent observability on top of the Stage-1 Website Understanding Engine.

## Purpose

Before and after each governed browser action the operator can now see concrete execution evidence instead of inferring behavior from the final report.

The live view displays:

- current HIP browser screenshot;
- current phase and agent intent;
- foreground drawer/dialog and selected tab;
- selected semantic control;
- ranked alternative controls and why they were rejected;
- currently mounted dropdown / Process Step choices;
- AutoWebGLM planner alignment;
- preferred and actual executor;
- PyAutoGUI/Playwright fallback reason;
- semantic effect verification;
- exact committed value when available.

The screenshot is captured with a temporary visual overlay around the selected target. The overlay uses `pointer-events:none` and is removed immediately after capture, so it cannot intercept HIP interactions.

## Safety / privacy boundary

The panel does **not** expose or persist private model chain-of-thought. It displays only concrete browser evidence and concise planner decision metadata.

Secrets are masked using the existing security helpers. CSS selectors, XPath expressions, screen coordinates and bounding boxes are intentionally omitted from `agent_live_view.json` so Stage-2 observability does not become brittle selector memory.

## Runtime files

Each run may contain:

```text
<run>/agent_live_view.json
<run>/agent_live_view/screenshots/*.png
```

The JSON keeps a bounded history plus the current action.

## Backend endpoints

```text
GET /api/mission/live-view
GET /api/mission/live-view/screenshot
```

The screenshot endpoint rejects absolute paths and traversal outside the resolved run directory.

## Browser runtime integration

`BrowserSession` owns an `AgentLiveViewRecorder`.

For a semantic browser action:

1. semantic candidates are resolved;
2. the chosen control is recorded;
3. a selected-target screenshot is captured;
4. AutoWebGLM alignment is recorded;
5. PyAutoGUI / Playwright executes the action;
6. execution provenance is recorded;
7. semantic effect/read-back is recorded;
8. an after-action screenshot is captured.

Every live-view operation is best-effort and non-blocking. Failure to record observability must never fail the actual HIP action.

## Control Center

The Mission tab now contains **Agent Live View**, which refreshes with the existing 3-second status cycle.

It includes:

- visual target screenshot;
- Intent / Phase / Expected value;
- active website state;
- selected target details;
- candidate ranking table;
- live dropdown option chips;
- AutoWebGLM planner section;
- executor section;
- verification section.

## Validation

Repository collection after Stage 2: **1,122 tests**.

Final non-overlapping batch result:

- Batch 1: 281 passed
- Batch 2: 280 passed, 1 environment-only skip
- Batch 3: 280 passed
- Batch 4: 280 passed

Total: **1,121 passed + 1 skipped = 1,122 accounted for, 0 failed**.

The one skip is the optional local Playwright Chromium launch test in this build container. Stage-1 synthetic CDP coverage and browser/runtime regressions remain green.

Stage-2 focused regression: **59 passed + 1 environment-only skip**.

## Stage boundary

Stage 2 is intentionally observability-first. Long-term learned website memory is not made authoritative here. That is Stage 3, after operator-visible target selection is stable and auditable.
