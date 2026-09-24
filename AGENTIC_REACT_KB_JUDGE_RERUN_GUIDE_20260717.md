# Rerun Guide — Agentic ReAct + KB + Judge Fix

Use the complete package as the new baseline. Preserve your existing `.env`, `config.yaml`, `uploads`, `golden_screenshots`, and `data/hip_memory/portal_brain`.

Run the same `run-full-dummy-fill` command. No additional flag is required.

## Expected phase transition

After Data Map completes, Source Document Type should produce a navigation trace containing:

- first observation: current Data Maps route, target Document Types route, executors agree, target not committed;
- plan: `navigate_target`;
- final observation: Document Types route committed and usable, executors agree;
- judge: pass.

The browser must not request SSO again unless Dell genuinely redirects to an authentication domain.

## Evidence to inspect

- `<phase>/mcp_runtime/navigation_react_trace.json`
- `<phase>/mcp_runtime/dual_mcp_same_surface.json`
- `<phase>/phase_execution_attempts.json`
- root `browser_session_manifest.json`
- root `browser_session_final_state.json`

Expected complete-run session contract:

```json
{
  "start_count": 1,
  "sso_prompt_count": 1,
  "single_persistent_context": true
}
```

An already authenticated Chrome profile may show `sso_prompt_count: 0`.
