# HIP Portal Agent v2.2.5 — Source Build Certification

Date: 2026-09-09

## Source verification

- Full pytest collection: **1052 tests**
- Full source regression: **1052 / 1052 PASS** (263 + 263 + 263 + 263)
- Focused affected compatibility set: **185 / 185 PASS**
- Dedicated v2.2.5 suite: **13 / 13 PASS**
- Python AST validation: **238 files / 0 errors**
- JavaScript syntax: **3 / 3 PASS**
- FastAPI import: **43 routes**
- Package version: **2.2.5**
- High-confidence source secret scan: **0 findings**
- Wheel build: **PASS**

## Wheel

`hip_portal_id_agent-2.2.5-py3-none-any.whl`

SHA-256:

`fad31f7f49cea35f3ed8905202b0297f9c6019ce4775226eda6cce6db347f35d`

## Scope

This source certification proves the implementation and automated contracts. The exact final ZIP is separately extracted and rerun before final release. The private Dell HIP tenant, Dell SSO, Dell AIA deployments and local MCP processes remain target-workstation runtime dependencies verified by the built-in live gates.
