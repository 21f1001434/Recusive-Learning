# HIP Portal Agent v1.7.1 — Existing-Venv AutoGen Readiness Fix

## What was fixed

The Streamlit mission gate no longer treats a damaged `importlib.metadata` record as proof that AutoGen is missing.

The runtime now verifies AutoGen 0.7.5 using layered detection:

1. `importlib.metadata.version()` when package metadata is healthy.
2. Exact `*.dist-info` directory version recovery when METADATA is damaged/incomplete.
3. Runtime imports of `AssistantAgent`, `autogen_core`, and `OpenAIChatCompletionClient`.

If exact 0.7.5 is present and imports succeed, the mission is enabled even when package metadata has recoverable damage. If an import fails, the UI shows the actual import/dependency error instead of saying AutoGen must be installed.

## Existing virtual environment

This release does not require a new virtual environment. The existing activated venv is used through `sys.executable`, and the AutoGen runtime panel displays the exact Python executable used for the check.

## Live Windows compatibility updates

- `websockets==13.1` is pinned for the Streamlit/Uvicorn stack observed in the live environment.
- `packaging>=23.2,<26` prevents the LangChain packaging conflict observed in the same venv.
- MCP package versions are aligned to the versions exposed by the Dell npm registry:
  - `@playwright/mcp@0.0.78`
  - `chrome-devtools-mcp@1.6.0`
- MCP command mode defaults to `auto`: Windows resolves `npx.cmd` first, then Bun/bunx as a fallback.
- Bun remains supported for `bun install`, `bun run ui`, `bun run platform`, and explicit Bun MCP scripts.

## Runtime behavior

The full seven-phase flow and all independent section scopes remain unchanged, including Transport Profile-only execution and repeatable `+` row handling.
