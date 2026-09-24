# HIP Portal Agent v2.2.7 — Source Build Certification

Date: 2026-09-09

## Source verification

- Full pytest collection: **1070 tests**
- Full source regression: **1070 / 1070 PASS** (268 + 268 + 267 + 267)
- Data Map / mission focused compatibility: **113 / 113 PASS**
- Initial v2.2.7 form-entry regression: **42 / 42 PASS**
- Python AST: **240 files / 0 errors**
- JavaScript syntax: **3 / 3 PASS**
- FastAPI import: **43 routes**
- Package version: **2.2.7**
- Wheel build: **PASS**

## v2.2.7 runtime fix

Data Maps Create Map is now modeled as an in-page form/drawer on the existing Data Maps route. The runtime has one ReAct-owned top-right `+ Add` transaction, rejects route-changing Add anchors, rejects row/menu/form-local Add controls, accepts query/hash-only drawer state, and treats a path change after Add as a wrong-target effect that must be recovered before retry. Initial Create Map proof no longer requires later/lazy upload-validation controls.

All prior v2.2.6 Chrome, Dell AIA text/vision, semantic/MCP, PyAutoGUI, Live Runtime Certification, Live GO/NO-GO, ReAct, BizFlow, and authoritative execution hardening remains included.
