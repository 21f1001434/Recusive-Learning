# HIP Portal v2.1.8 Build Certification

## Source-tree verification

- Version: **2.1.8**
- Full repository regression: **940 / 940 PASS** (4 x 235-test partitions)
- New PyAutoGUI MCP regression suite: **25 / 25 PASS**
- Python AST validation: **228 files / 0 errors**
- JavaScript syntax validation: **3 files / 0 errors**
- FastAPI import: **42 routes**
- Wheel: `hip_portal_id_agent-2.1.8-py3-none-any.whl`
- Wheel SHA-256: `15d58665146cbf613143dc56c202aecf9ac1ff4323e13c1c7ca3140190ff4383`

## v2.1.8 architecture

- AutoWebGLM remains the primary planner.
- HIP Intelligence MCP remains the website-specific semantic brain.
- Official Playwright MCP remains the sole governed executor for normal HIP web controls.
- Chrome DevTools MCP remains the independent DOM/network/console witness.
- PyAutoGUI MCP is an optional tertiary Windows desktop/native fallback only.
- Final Save/Create/Submit/Delete/Deploy/Publish/Update mutations remain disabled through PyAutoGUI MCP/local PyAutoGUI by default.
- Native coordinate recovery requires explicit high-confidence evidence (default >=0.97).
- Direct in-process PyAutoGUI is compatibility-only when the MCP channel is unavailable and the config permits it.

## Live-environment boundary

This build certification does not claim that Dell SSO, the live HIP tenant, Dell AIA, Edge/Chrome, or external MCP processes are reachable from this packaging environment. The application's Live GO/NO-GO gate proves those dependencies on the target Windows machine.
