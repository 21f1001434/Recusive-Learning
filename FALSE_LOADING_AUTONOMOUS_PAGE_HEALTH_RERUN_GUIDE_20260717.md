# Rerun Guide

1. Extract the complete package into a new directory.
2. Copy only your existing `.env`, `config.yaml` values, uploads, golden screenshots, and `data/hip_memory/portal_brain` when required.
3. Keep the new loading settings from the delivered `config.yaml`.
4. Run the same `run-full-dummy-fill` command. No new CLI flag is required.

Expected behavior:

- Passive DDS spinner/progress/aria-busy nodes do not pause filling.
- A real overlay must be confirmed twice before the 120-second timer starts.
- If a real overlay survives for 120 seconds, the same page refreshes once without losing SSO.
- The interrupted phase is reconstructed from its phase-local `input.json` branch.

Inspect:

- `mcp_runtime/autonomous_page_health/`
- `mcp_runtime/loading_watchdog/`
- `runtime_self_heal/runtime_self_heal_summary.json`
