# Verified Live Baseline Changes — 2026-07-20

## Audit result before changes

- Python compilation: passed
- Existing suite: 410/410 passed
- Authenticated Dell HIP execution: not possible in this environment

## Issues found and fixed

1. The CLI `--require-mcp` switch enforced only Playwright MCP and Chrome DevTools MCP. It now also enables and requires HIP Intelligence MCP.
2. The bundled configs used one developer-specific OneDrive runs path. They now use portable `./runs` and the live runner defaults to `C:\hip_runs`.
3. MCP commands assumed Node.js existed at `C:\Program Files\nodejs`. They now use `npx.cmd` from `PATH`.
4. The example and strict configs omitted portal-learning/runtime-self-heal sections present in the active config. All three configs are now aligned.
5. README statements that no local MCP exists were stale after HIP Intelligence MCP was added. They now distinguish existing browser MCPs from the included browserless intelligence MCP.
6. The `.env.example` vision placeholder contained a duplicated malformed token. It was corrected.
7. Added `run_live_full_dummy.ps1` and a focused live-testing guide.

## Files changed

- `hip_id_agent/cli.py`
- `config.yaml`
- `config.example.yaml`
- `config.mcp-required.windows.yaml`
- `.env.example`
- `README.md`
- `tests/test_course_agentq_architecture_integration.py`
- `tests/test_chrome_first_config.py`
- `run_live_full_dummy.ps1`
- `LIVE_TESTING_README_20260720.md`
- `VERIFIED_LIVE_BASELINE_CHANGES_20260720.md`
