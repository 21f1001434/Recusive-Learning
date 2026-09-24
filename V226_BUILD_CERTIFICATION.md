# HIP Portal Agent v2.2.6 — Source Build Certification

Date: 2026-09-09

## Source-tree verification

- Full pytest collection: **1062 tests**
- Full source regression: **1062 / 1062 PASS** (266 + 266 + 265 + 265)
- Focused PyAutoGUI/Live Readiness/Form Entry/Authoritative Execution compatibility: **92 / 92 PASS**
- Dedicated v2.2.6 tests: **10 / 10 PASS**
- Python AST validation: **239 files / 0 errors**
- JavaScript syntax: **3 / 3 PASS**
- FastAPI import: **43 routes**
- Package version: **2.2.6**
- High-confidence source secret scan: **0 findings**
- Local `.env`, `credentials.json`, `secrets.json` in release source: **0**
- Wheel build: **PASS**
- Wheel SHA-256: `87e0507379a176387b89ffce40aa395c54e80cb98b061b6f6780e9aa1a3dbd08`

## v2.2.6 runtime scope

This release keeps Playwright MCP as the normal governed HIP web executor and makes PyAutoGUI MCP an active audited recovery channel after a semantically proven Playwright MCP action fails. Safe/structural clicks, business-field fills, and key actions can use PyAutoGUI MCP before the local in-process Playwright compatibility fallback; exact browser effect/value verification is still mandatory and final tenant mutation clicks remain disallowed through PyAutoGUI by default.

Live GO/NO-GO now auto-renews a missing/expired/mismatched real Windows runtime certificate when prerequisite static/browser/text/vision/runs-path checks are healthy and no mission is running. The renewal is the same non-mutating Chrome + Dell SSO + Playwright/DevTools/HIP Intelligence + Dell AIA + PyAutoGUI MCP certification; it does not bypass the workstation proof.
