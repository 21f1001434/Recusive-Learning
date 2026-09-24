# v1.8.7 Edge + Vision + AutoWebGLM Runtime Architecture

## Primary browser
The HIP agent launches Microsoft Edge Stable through Playwright channel `msedge` and keeps one persistent profile in `data/edge_profile`. Playwright MCP, Chrome DevTools MCP (CDP), Browser Use, the read-only LangChain Playwright toolkit, AutoWebGLM recovery, vision perception and PyAutoGUI all refer to this same authenticated Edge surface.

## Vision model
The final SectionJudge already requires a multimodal vision model. v1.8.7 also adds `VisionRuntimeBridge` before completion. It performs an actual two-pixel image-understanding probe, captures the current Edge viewport for recovery, and returns structured value-free visual state. It never receives cookies/auth headers and is not an ungoverned click executor.

## Five-minute loading rule
The DOM watchdog first proves that a loader geometrically blocks the active surface; passive DDS spinners are ignored. The same blocking fingerprint must remain for consecutive observations and continuously exceed 300 seconds. At that boundary the vision model independently confirms that a loading surface is still visible, blocking, and the main form is not usable. Only then can the agent refresh Edge once. If an unsaved form was active, a value-free replay marker is written and the outer phase restarts from the current `input.json`.

## AutoWebGLM compatibility
The production recovery bridge implements the applicable runtime protocol from THUDM/AutoWebGLM: task description, simplified HTML, viewport position, bounded action history, and all ten official action forms: click, hover, select, type_string, scroll_page, go, jump_to, switch_tab, user_input, finish. Proposals pass AgentQ reward/safety gating and mutation governance. The original ChatGLM3-6B weights, hybrid-training pipeline, rejection-sampling training data and AutoWebBench datasets are research/training assets, not required production runtime dependencies. A native model wrapper can still be attached through `autowebglm.native_model_command`.

## Recovery hierarchy
1. Deterministic HIP expert skill and same-family validated memory.
2. Exact DOM/MCP action and stable verification.
3. Browser Use + LangChain read-only semantic context + vision screenshot analysis.
4. AutoWebGLM single-action proposal, filtered by AgentQ reward and governance.
5. PyAutoGUI last-resort physical interaction on a Playwright-resolved control.
6. Exact second stable DOM read and text/vision section judge.
