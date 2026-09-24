# HIP Portal V237 / v2.3.7 — Final Exact-Package Re-verification

## Scope

This release was re-verified from a fresh extraction of the V237 archive after the runtime-input/golden-reference hardening. The final rebuild also removes stale release labels in the README and local UAT report; the UAT report now derives its displayed version from `hip_id_agent.__version__`.

## Runtime guarantees rechecked

- Actual runtime `input.json` is the value authority.
- Every applicable nonblank runtime input leaf is accounted for on adaptive fill cycles.
- Missing static-graph fields can become transient semantic execution nodes from current live control evidence.
- Newly revealed input-owned controls force another execution cycle before phase success.
- Exact readback + authoritative execution proof remains required in strict live mode.
- Golden screenshots are structural/visual guidance only; they never supply customer values.
- BizFlow accounting is tab-aware across Flow Details, Configure Source(s), Configure Target(s)/Process Steps, and Configure Routing.
- Browser-Use-style observation cannot own/stop the HIP-managed browser session.
- Persistent memory never authorizes an action without current live evidence.

## Full regression

- Collected: **1,182**
- Passed: **1,181**
- Skipped: **1** environment-dependent browser test
- Failed: **0**

## Packaged input/golden audit

Using `examples/uhaul_poasn_full_dummy_input.json`, the runtime input ledger enumerates:

- Data Map: 7 nonblank leaves
- Source Document Type: 30
- Target Document Type: 30
- Rule: 19
- Source Transport Profile: 15
- Target Transport Profile: 15
- BizFlow: 61

Golden reference folder contains 12 PNG references for the U-HAUL PO/ASN flow.

## Browser UAT

Local Chromium final-mission UAT: **PASS, 7/7 phases**.

BizFlow lifecycle: **Edit / Save / Validate / Deploy = PASS**.

The report explicitly states `dell_environment_contacted=false`; this is local certification, not a claim of Dell-tenant execution.

## Build/package checks

- Python compileall: PASS
- `webui/app.js` Node syntax: PASS
- `config.yaml`: YAML parse PASS
- `config.example.yaml`: YAML parse PASS
- `config.mcp-required.windows.yaml`: YAML parse PASS
- v2.3.7 wheel rebuilt with pip PEP-517 no-build-isolation path: PASS
- wheel clean target install/import: PASS
