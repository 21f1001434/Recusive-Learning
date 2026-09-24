# V233 Final Build Verification

Release package version: **2.3.3**

## Complete regression

Collected tests: **1,154**

Final explicit results after the 2.3.3 version bump:

- Passed: **1,153**
- Skipped: **1**
- Failed: **0**

The single skip is the pre-existing optional local Chromium-binary environment check. The dedicated final mission Chromium UAT passed separately using `/usr/bin/chromium` in the release environment.

## Final mission browser UAT

`python -m hip_id_agent.cli certify-final-mission --config config.yaml`

Result: **PASS**

- Data Map: PASS
- Source Document Type: PASS
- Target Document Type: PASS
- Rule: PASS
- Source Transport Profile: PASS
- Target Transport Profile: PASS
- BizFlow: PASS
- Edit: PASS
- Save: PASS
- Validate: PASS
- Deploy: PASS
- Final BizFlow status: `Deployed`
- Final mission consolidation gate: PASS
- PyAutoGUI-MCP interaction contract: exercised
- Dell environment contacted: NO

## Static/release surface

- Python files compiled: **110 / 110**
- `config.yaml`: PASS
- `config.example.yaml`: PASS
- `config.mcp-required.windows.yaml`: PASS
- `webui/app.js`: syntax PASS
- `webui/server.js`: syntax PASS
- `webui/platform.js`: syntax PASS
- CLI `certify-final-mission`: present

## SFTP-HAFT role policy

Canonical UHAUL input verifies:

- Source/Sender: `da-sender-sftphaft-dce-shared`
- Target/Receiver: `pt-receiver-sftphaft-dce-shared`

## Packaging

Wheel: `hip_portal_id_agent-2.3.3-py3-none-any.whl`

The final ZIP is created from a cache-clean distribution tree and is then extracted into a fresh directory for package-level smoke/UAT verification before release.
