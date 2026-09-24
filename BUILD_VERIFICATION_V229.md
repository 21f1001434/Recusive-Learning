# V229 Build Verification — 2026-09-10

## Build identity

- Package: `hip-portal-id-agent`
- Version: `2.2.9`
- Architecture: AutoWebGLM semantic planner + PyAutoGUI MCP preferred executor + Playwright MCP authoritative fallback + Python Playwright compatibility fallback
- Default PyAutoGUI MCP requirement: optional (`config.yaml`)
- Explicit strict PyAutoGUI profile: `config.mcp-required.windows.yaml`

## Static checks

- `python -m compileall -q hip_id_agent backend frontend streamlit_app.py` — PASS
- `config.yaml` parse — PASS
- `config.example.yaml` parse — PASS
- `config.mcp-required.windows.yaml` parse — PASS
- package version in `pyproject.toml` — `2.2.9`
- package version in `hip_id_agent/__init__.py` — `2.2.9`

## Full regression suite

Pytest collection: **1,096 tests**.

The suite was executed in four non-overlapping filename batches to avoid a long single-process runner stall:

- Batch 1: **276 passed**
- Batch 2: **249 passed**
- Batch 3: **313 passed** (`152 + 161`, split only to isolate a runner stall; file sets are non-overlapping)
- Batch 4: **258 passed**

Total: **1,096 / 1,096 passed**.

Logs:

- `V229_TEST_BATCH_1.txt`
- `V229_TEST_BATCH_2.txt`
- `V229_TEST_BATCH_3A.txt`
- `V229_TEST_BATCH_3B.txt`
- `V229_TEST_BATCH_4.txt`

## Targeted runtime regressions

The following targeted group was rerun after final hybrid-selector hardening:

- `tests/test_v229_hybrid_interaction_completion.py`
- `tests/test_v228_local_mock_pyautogui_primary.py`
- `tests/test_pyautogui_fallback_and_exact_fill.py`
- `tests/test_v227_all_sections_in_page_contract.py`

Result: **29 / 29 passed**.

The targeted group covers, among other things:

- default hybrid configuration
- rejection of human action descriptions as CSS
- canonical unique selector generation from a proven Locator
- strict completion rejecting visible values without authoritative executor proof
- strict completion accepting exact authoritative transactions
- blocked-phase continuation not being presented as verified handoff
- local Chromium same-page section flows
- PyAutoGUI primary/fallback behavior
- all-section in-page route contracts

## Wheel

The wheel is built with local build isolation disabled because this execution environment has no external internet access for fetching build dependencies. The project source itself is unchanged by that build mode.

Expected wheel in final package:

`dist/hip_portal_id_agent-2.2.9-py3-none-any.whl`

Wheel SHA-256: `0286b1a7aa338d90b55b49473f732268801570ff04ec6c9d42f33ec7efb705ea`

## Scope of certification

This build is source/regression certified in the available Linux/container environment, including real local Chromium tests. It is **not** claimed as a successful Dell tenant UAT because the actual Dell portal, Windows desktop, Dell SSO, corporate network and live PyAutoGUI MCP desktop are not available inside this build environment.

The next real Windows/Dell run is the authoritative UAT for desktop-coordinate execution and current Dell DDS behavior. V229 is designed so that failure/unavailability of PyAutoGUI itself falls through to Playwright MCP rather than blocking the form task.
