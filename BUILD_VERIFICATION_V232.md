# Build Verification — V232

Version: **2.3.2**

## Repository regression

Collected tests: **1,115**

- Batch 1: 279 passed
- Batch 2: 279 passed
- Batch 3: 279 passed
- Batch 4: 278 passed
- Total: **1,115 / 1,115 passed**

The first monolithic verification attempt surfaced two stale V231 assertions (autonomous mission forcing strict MCP; all normal configs requiring HIP Intelligence MCP). Those expectations were updated to the V232 adaptive contract. A real section CLI inconsistency discovered during the same pass was also fixed: `run-section` now defaults to `--allow-executor-fallback` and no longer emits the obsolete `--allow-playwright-fallback` flag. The complete suite was then recollected and all tests passed.

## Static/build checks

- `hip_id_agent`, `backend`, and `frontend` Python trees compile successfully.
- `config.yaml`, `config.example.yaml`, and `config.mcp-required.windows.yaml` parse successfully.
- Normal configs: `strict_runtime_required=false`, `hip_intelligence_mcp_required=false`, all-phase autonomous runtime enabled.
- Strict Windows config: `strict_runtime_required=true`, `hip_intelligence_mcp_required=true`, all-phase autonomous runtime enabled.
- CLI help exposes `--allow-executor-fallback` for both full and independent section execution.
- No source reference to the obsolete `--allow-playwright-fallback` flag remains.
- `webui/app.js`, `webui/server.js`, and `webui/platform.js` pass JavaScript syntax validation with Node. Bun itself is not installed in this build container, so the Bun server was not launched here.
- Wheel built with local setuptools/wheel using `pip wheel --no-deps --no-build-isolation`.

## Environment limitation

This build container cannot authenticate to Dell HIP and cannot provide a Windows visible desktop for the real PyAutoGUI MCP server. Therefore the result certifies code/tests/package behavior, not Dell tenant UAT. The package keeps non-mutating live runtime certification and GO/NO-GO checks for the target Windows workstation.

## Packaged-artifact smoke verification

After creating the clean final distribution, the ZIP was extracted to a new directory. The extracted copy successfully imported `hip_id_agent` version `2.3.2`, loaded both adaptive and strict configurations, confirmed autonomous enablement for all seven phases, and passed the V227/V230/V231/V232 plus independent-section focused package suite: **54 / 54 passed**.

Wheel SHA-256: `245ed6db197718b064e0848b8cc3bc762c078ea46fb70a32de93ff060e536cde`.
