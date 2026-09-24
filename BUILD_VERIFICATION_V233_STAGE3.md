# V233 Stage 3 Build Verification

Date: 2026-09-11

## Repository regression

Collected tests: **1,134**

- Batch 1: 284 passed
- Batch 2: 284 passed
- Batch 3: 283 passed
- Batch 4: 282 passed, 1 skipped

Total: **1,133 passed + 1 environment-only skip, 0 failed**.

The skip is the optional local Playwright Chromium launch test because the build container does not contain the Playwright Chromium browser binary. Synthetic CDP/event-listener coverage and the remaining browser/runtime test suite pass.

## Focused Stage-3 + TP/input contract validation

- Persistent world-model safety, trust, dependency and portal-choice tests: passed.
- Transport Profile KB and state graph role-aware deployment-group tests: passed.
- Full input-contract hardening tests: passed.
- Explicitly verified SFTP-HAFT role mapping:
  - Sender/Dell/source → `da-sender-sftphaft-dce-shared`
  - Partner/Receiver/target → `pt-receiver-sftphaft-dce-shared`

## Static checks

- Python modules compiled: **107**, failures: 0.
- `config.yaml`: parsed, world model enabled.
- `config.example.yaml`: parsed, world model enabled.
- `config.mcp-required.windows.yaml`: parsed, world model enabled.
- `webui/app.js`: Node syntax check passed.
- `webui/server.js`: Node syntax check passed.
- `webui/platform.js`: Node syntax check passed.

## Safety assertions

Tests verify that Stage-3 memory does not retain:

- selectors/XPath;
- generated DDS ids;
- screen or viewport coordinates;
- bounding boxes;
- arbitrary customer-entered values.

Portal-owned dropdown values are learned only when the selected label exactly matches a live mounted option observed before the verified action.

## Package version

The staged V233 rollout deliberately keeps the internal Python package version at `2.3.2` so the existing version-pinned V232 regression suite remains valid. The distribution artifact name identifies it as **V233 Stage 3**.
## Exact artifact smoke verification

The exact Stage-3 ZIP was freshly extracted and verified before release:

- package version import: `2.3.2`
- world model enabled for ranking/planning: PASS
- UHAUL source SFTP-HAFT deployment group: `da-sender-sftphaft-dce-shared`
- UHAUL target SFTP-HAFT deployment group: `pt-receiver-sftphaft-dce-shared`
- Stage 1 + Stage 2 + Stage 3 extracted-package tests: **18 passed, 1 environment-only skip**
- wheel installed into an isolated target and imported successfully

Wheel SHA-256: `cc07bf2be0e67302288a50f9449875a1fcbb838ee5bc0ae3214c9f882d6140dc`

