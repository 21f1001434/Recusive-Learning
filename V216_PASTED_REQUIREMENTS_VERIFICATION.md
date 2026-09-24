# HIP Portal Agent v2.1.6 — Pasted Requirements Verification

## Scope

This release re-audits the exact `Pasted markdown(6).md` hardening requirements against executable source and tests. The pasted file is byte-for-byte identical to the earlier `Pasted markdown(5).md` used during the v2.1.5 convergence work.

## Finding and implementation

One enforcement gap remained in v2.1.5: `open_control_for_discovery()` performed semantic proof and AutoWebGLM gating, attempted Playwright MCP, but could fall back to raw `locator.click()` if Playwright MCP execution failed. That contradicted the pasted requirement that read-only dropdown discovery itself execute as:

`semantic proof -> AutoWebGLM -> Playwright MCP -> post-action effect verification`

v2.1.6 closes that gap. Under the Layer-11 semantic runtime, failed/unavailable Playwright MCP execution now fails closed and cannot fall through to raw Playwright/DOM click. The raw locator fallback remains only for standalone/offline pages without the governed semantic runtime.

## 25/25 requirement traceability suite

`tests/test_v216_pasted_requirements_traceability.py` contains exactly 25 tests and passes 25/25. It covers:

1. section-only text cannot prove a specific control;
2. specific control labels can prove a control;
3. Data Map legacy setter fail-closed guard;
4. Document Type legacy setter fail-closed guard;
5. Rules legacy setter fail-closed guard;
6. Transport Profile legacy setter fail-closed guard;
7. Transport Profile DDS combobox guard order;
8. independent DOM click telemetry in Live Witness;
9. blocked mutation attempts are Witness violations;
10. page-level mutation authorization and structural-opener provenance;
11. generation-scoped repeatable-row binding changes after rerender;
12. repeatable-row binding is deterministic within one generation;
13. ambiguous repeatable controls fail closed after rerender;
14. `:nth-child()` selectors are generation volatile;
15. `:nth-of-type()` selectors are generation volatile;
16. sticky restoration rejects generation-volatile selectors under semantic runtime;
17. read-only discovery never raw-clicks after Playwright MCP failure under semantic runtime;
18. successful read-only Playwright MCP discovery runs post-action effect verification;
19. HIP Intelligence MCP evidence/consensus is required by default;
20. Live GO/NO-GO rejects missing Playwright MCP tool inventory;
21. Live GO/NO-GO rejects missing DevTools MCP tool inventory;
22. Live GO/NO-GO rejects missing HIP Intelligence MCP tool inventory;
23. DOM-generation advancement alone cannot prove action success;
24. AutoWebGLM protocol preserves `press_key` intent;
25. Document Type/Rules/Transport Profile Add recovery and BizFlow structural actions remain semantically governed.

## Regression and release validation

- Dedicated pasted-requirements suite: **25/25 PASS**.
- Full repository: **890/890 PASS** in four deterministic partitions: 224 + 200 + 248 + 218.
- Python AST parse: **224 files / 0 errors**.
- JavaScript `node --check`: **PASS** for all shipped JS files.
- FastAPI import: **42 routes**.
- Focused Playwright MCP + Chrome DevTools MCP + Layer-11 + pasted-requirements suite: **61/61 PASS**.
- Wheel: `hip_portal_id_agent-2.1.6-py3-none-any.whl`, import reports **2.1.6** and **42 FastAPI routes**.

## Runtime boundary

Repository tests and packaging can verify the implementation and fail-closed contracts. Actual Dell HIP tenant connectivity, Dell SSO, real Edge/Chrome startup on the target Windows host, and live Playwright MCP / Chrome DevTools MCP / HIP Intelligence MCP / Dell AIA model endpoints remain environment-dependent and are therefore enforced by the application's live GO/NO-GO gate before a governed mission starts.
