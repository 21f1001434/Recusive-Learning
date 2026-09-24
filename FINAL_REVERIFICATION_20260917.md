# HIP Portal V235 / 2.3.5 — Final Re-verification

Date: **2026-09-17**

## Release decision

**PASS for corrected source/package certification.** The live failure evidence supplied on 2026-09-17 was reproduced at the policy/classification level and the two root causes were patched: exact semantic-anchor loss/over-strict structural navigation gating, and Browser-Use lifecycle interference with the HIP-managed browser session.

## Test accounting

The suite was run in explicit non-overlapping file groups because the monolithic command exceeds the execution window. Final accounting after the v2.3.5 version bump:

| Group | Result |
|---|---:|
| test files 0–29 | 234 passed |
| test files 30–59 | 184 passed |
| test files 60–89 | 182 passed |
| test files 90–104 | 127 passed |
| test files 105–119 | 93 passed |
| test files 120–134 | 248 passed |
| test files 135–141 | 36 passed |
| test files 142–149 | 55 passed, 1 skipped |
| test files 150–end | 9 passed |
| **Total** | **1,169 passed, 1 skipped, 0 failed / 1,170 collected** |

The skipped test is an existing environment-dependent browser check; it is not a V235 failure.

Focused V235 + V234 + V233 runtime regression: **51 passed, 0 failed**.

## Static/build checks

- `python -m compileall -q hip_id_agent backend tests`: PASS.
- `node --check webui/app.js`: PASS.
- Package version: `2.3.5` in `pyproject.toml` and `hip_id_agent.__version__`.

## Local browser final-mission UAT

The local Chromium UAT passed all seven phases and the BizFlow lifecycle. It exercised the same-page/in-page flow, dynamic portal-owned choices, the PyAutoGUI-MCP point interaction contract, and persistent semantic world-model learning.

- Data Map: PASS
- Source Document Type: PASS
- Target Document Type: PASS
- Rule: PASS
- Source Transport Profile: PASS
- Target Transport Profile: PASS
- BizFlow: PASS
- Edit / Save / Validate / Deploy: PASS
- final status: `Deployed`
- source SFTP-HAFT group: `da-sender-sftphaft-dce-shared`
- target SFTP-HAFT group: `pt-receiver-sftphaft-dce-shared`
- `dell_environment_contacted`: `false`

The local UAT is deliberately non-Dell and must not be interpreted as a live tenant mutation certificate.

## Changed runtime files

- `hip_id_agent/semantic_control.py`
- `hip_id_agent/browser_use_bridge.py`
- `hip_id_agent/browser_session.py`
- `hip_id_agent/runtime_self_heal.py`
- `hip_id_agent/config.py`
- `config.yaml`
- `config.example.yaml`
- `config.mcp-required.windows.yaml`
- `tests/test_v235_live_failure_20260917.py`
- release/version/readme/changelog metadata

See `V235_LIVE_FAILURE_FIX_20260917.md` for root-cause details and execution policy.
