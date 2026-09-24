# HIP Portal Agent v2.2.4 — Source Build Certification

Date: 2026-09-09

## Source-tree verification

- Full pytest collection: **1039 tests**
- Full promoted source regression: **1039 / 1039 PASS** (260 + 260 + 260 + 259)
- Focused v2.2.4 + compatibility set: **84 / 84 PASS**
- Python AST validation: **237 files / 0 errors**
- JavaScript syntax: **3 / 3 PASS**
- FastAPI import: **43 routes**
- Package version: **2.2.4**
- High-confidence source secret scan: **0 findings**
- Local `.env`, credentials.json, secrets.json files in source tree: **0**
- Wheel build: **PASS**
- Wheel SHA-256: `9ae438b6f57c9e9bec3a247a687c09302a19da9afae4fc79cb0dcd82e82be738`

## Release scope

v2.2.4 makes form opening a mandatory, effect-verified autonomous transaction for Data Map, Document Type, Rule, Transport Profile and BizFlow. Standard phases prove listing -> top-right + Add -> create form before filling. BizFlow proves listing -> + Add -> template/card picker -> template link/action -> multi-tab create form and then proves each requested tab active before filling. Failures raise recoverable active-surface errors and are handled by the existing bounded ReAct self-heal path. v2.2.3 Chrome-first/UTF-8/semantic-judge hardening and v2.2.2 uncapped Dell AIA text/vision behavior remain included.

This source certificate does not claim live access to the private Dell SSO/HIP tenant. Target-machine proof remains the responsibility of Live Runtime Certification and Live GO/NO-GO.
