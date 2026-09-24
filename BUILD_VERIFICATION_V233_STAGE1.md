# V233 Stage 1 Build Verification

- Stage: Deep website understanding before action
- Base package version kept at: `2.3.2` during staged rollout
- New tests collected: 3 (one optional real-Chromium test may skip when browser binary is absent)
- Total repository tests collected: 1118

## Full non-overlapping regression

- Batch 1: 298 passed
- Batch 2: 246 passed
- Batch 3: 276 passed
- Batch 4: 297 passed, 1 skipped
- Total: **1117 passed, 1 environment-only skip, 0 failed**

The skip is `test_stage1_real_chromium_foreground_drawer_and_detached_dds_options` when a Playwright Chromium binary is not installed. Synthetic CDP/event-listener coverage for the same model passed.

## Static validation

- `hip_id_agent` compile: PASS
- `backend` compile: PASS
- `frontend` compile: PASS
- `config.yaml`: PASS
- `config.example.yaml`: PASS
- `config.mcp-required.windows.yaml`: PASS

## Key Stage-1 assertions

- foreground modal/drawer outranks underlying visible detail controls
- background visible controls remain represented but are marked outside active surface
- `aria-controls`/`aria-owns` popup ownership is represented
- Process Step/dropdown options are catalogued
- registered event listener types can be captured through CDP
- event/mutation timeline remains available
- no portal mutation is performed by website-understanding capture
