# V233 Stage 2 Build Verification

## Scope

Artifact: `HIP_PORTAL_V233_STAGE2_AGENT_LIVE_VIEW_1121_PASS_20260911.zip`

Internal package version remains `2.3.2` during the staged V233 rollout to retain compatibility with existing version-pinned regression tests.

## Automated tests

Collected: 1,122 tests.

| Batch | Result |
|---|---:|
| 1 | 281 passed |
| 2 | 280 passed, 1 skipped |
| 3 | 280 passed |
| 4 | 280 passed |
| **Total** | **1,121 passed, 1 skipped, 0 failed** |

Focused Stage 1 + Stage 2 + V227–V232 runtime regression: 59 passed, 1 skipped.

Stage-2-specific tests: 4 passed.

## Static validation

- Python compilation: 105 source modules passed.
- `config.yaml`: parsed successfully.
- `config.example.yaml`: parsed successfully.
- `config.mcp-required.windows.yaml`: parsed successfully.
- `webui/app.js`: Node syntax check passed.
- `webui/server.js`: Node syntax check passed.
- `webui/platform.js`: Node syntax check passed.

## Wheel

Wheel built offline with the environment's installed build toolchain using `pip wheel --no-build-isolation`.

`hip_portal_id_agent-2.3.2-py3-none-any.whl`

SHA-256: `ba058060f126fb247ecaf87b2e11569b36ea0b78bd000ad1f00ca78d2671f41f`

## New Stage-2 components

- `hip_id_agent/agent_live_view.py`
- BrowserSession action-selection/execution hooks
- `/api/mission/live-view`
- `/api/mission/live-view/screenshot`
- Mission-tab Agent Live View UI
- candidate ranking / rejection rendering
- current dropdown option rendering
- executor/fallback rendering
- semantic verification rendering
- `tests/test_v233_stage2_agent_live_view.py`

## Non-claims

This certification validates code, local browser abstractions, synthetic CDP behavior and regression contracts. It is not a claim that a Dell HIP production tenant has been executed successfully from this build environment.
