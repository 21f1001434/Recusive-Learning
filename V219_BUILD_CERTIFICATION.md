# HIP Portal v2.1.9 — Build Certification

## Implemented missing release/runtime step

v2.1.9 adds an executable Windows live-runtime certification gate. It converts the former external-only boundary into a product capability:

- headed persistent Edge/Chrome launch;
- Dell SSO completion/reuse;
- same-browser Playwright MCP proof;
- same-surface Chrome DevTools MCP witness proof;
- HIP Intelligence MCP semantic-tool proof;
- Dell AIA text and vision live probes;
- PyAutoGUI MCP read-only desktop smoke using only size, position and screenshot;
- mutation authorization explicitly disabled;
- expiring runtime-fingerprinted certificate with canonical SHA-256 integrity;
- existing Live GO/NO-GO consumes the certificate and the shipped config requires it;
- CLI, FastAPI and JavaScript UI integration.

## Source-tree verification

- Repository tests collected: **960**
- Final source partitions: **240 + 240 + 240 + 240 = 960/960 PASS**
- New v2.1.9 certification tests: **20/20 PASS**
- Existing live-readiness/witness/PyAutoGUI/UI focused compatibility: **74/74 PASS**
- Python AST: **230 files, 0 errors**
- JavaScript syntax: all shipped `webui/*.js` files pass `node --check`
- FastAPI import: **43 routes**
- Package version: **2.1.9**
- Wheel build: PASS

## Runtime boundary

This build can execute the live certification on the user's actual Windows HIP workstation, but this sandbox cannot itself prove Dell SSO, the private HIP tenant, local Edge/Chrome, Dell AIA endpoints or desktop MCP processes. The new `certify-live-runtime` command and Web UI control perform that proof on the target workstation and issue a fail-closed certificate consumed by Live GO/NO-GO.
