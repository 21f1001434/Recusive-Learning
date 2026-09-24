# V233 Stage 4 Build Verification

Date: 2026-09-11

## Repository regression

Collected tests: **1,147**

- Batch 1: 287 passed
- Batch 2: 287 passed
- Batch 3: 287 passed
- Batch 4: 285 passed, 1 skipped

Total: **1,146 passed + 1 environment-only skip, 0 failed**.

The skip is the optional local Playwright Chromium launch test because the build container does not contain the Playwright Chromium browser executable. Synthetic CDP/event-listener coverage, semantic browser tests, PyAutoGUI/Playwright fallback coverage, and the remaining runtime suite pass.

## Focused release regression

Fresh focused Stage 1–4 + certified future-task run before packaging:

- **41 passed + 1 environment-only skip, 0 failed**.

Earlier Stage-4 integration/certified-path validation during implementation:

- Stage 1–4 integration: **95 passed + 1 environment-only skip**.
- Certified semantic-action/mutation-safe path: **39 passed**.
- Stage-4 core tests: **11 passed**.

## Static checks

- Python modules compiled: **108**, failures: 0.
- `config.yaml`: operational world model enabled.
- `config.example.yaml`: operational world model enabled.
- `config.mcp-required.windows.yaml`: operational world model enabled.
- In all shipped profiles:
  - `dynamic_option_resolution_enabled=true`
  - `transition_planning_enabled=true`
  - `transition_plan_requires_live_reproof=true`
  - `mutation_governance_always_required=true`
- `webui/app.js`: Node syntax check passed.
- `webui/server.js`: Node syntax check passed.
- `webui/platform.js`: Node syntax check passed.
- `examples/uhaul_poasn_full_dummy_input.json`: JSON parse passed.
- `knowledge_base/HIP_Unified_Deep_KB.json`: JSON parse passed.

## SFTP-HAFT deployment-group verification

The canonical UHAUL input contains:

- Source/Sender/Dell: `da-sender-sftphaft-dce-shared`
- Target/Partner/Receiver: `pt-receiver-sftphaft-dce-shared`

Both values are also present in the live acceptance contract section of the canonical input.

## Operational contract verification

The exported Stage-4 contract reports:

- supported goal actions include Create/Edit/Save/Validate/Clone/Migrate/Deploy/Add Row;
- `memory_proposes_live_evidence_authorizes=true`;
- `mutation_governance_preserved=true`.

## Package version

The staged V233 rollout deliberately keeps the internal Python package version at `2.3.2` so the existing version-pinned V232 regression suite remains valid. The distribution artifact name identifies it as **V233 Stage 4**.

## Exact artifact smoke verification

A clean Stage-4 distribution ZIP was created, freshly extracted into a new directory, and validated against the extracted copy.

- package version import: `2.3.2`
- Stage 1 + Stage 2 + Stage 3 + Stage 4 + certified semantic-action + TP/input-contract smoke: **73 passed, 1 environment-only skip, 0 failed**
- operational goal-action contract import: PASS
- normal/example/strict config Stage-4 flags: PASS
- source SFTP-HAFT deployment group: `da-sender-sftphaft-dce-shared`
- target SFTP-HAFT deployment group: `pt-receiver-sftphaft-dce-shared`
- wheel installed into an isolated target and imported successfully

Wheel SHA-256: `c684daaa198954d004a9e45867169b158d73b26c22416e8cbccdc9fb3667fa35`

The final ZIP SHA-256 is reported outside the archive because embedding a ZIP's own final hash would change the artifact.
