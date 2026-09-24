# FULL DUMMY FILL E2E — MCP REQUIRED FIX VERDICT

## Verdict
Fixed.

This patch makes the full dummy-fill runner MCP-required and fixes the Windows/OneDrive path failure seen in the Transport Profile phase.

## User error addressed
The run failed while writing:

`source_transport_profile/transport_profile_kb/old_transport_profile_id_lookup_by_name.json`

on Windows. This is consistent with a deep OneDrive workspace path hitting Windows MAX_PATH behavior even though the code created the parent folder.

## Fixes added

1. `run-full-dummy-fill` now has `--require-mcp/--allow-playwright-fallback`.
2. Default behavior for this runner is MCP required.
3. It validates the pre-existing Chrome DevTools MCP before opening HIP forms.
4. If MCP is unavailable, the run fails before filling any form.
5. It writes `mcp_preflight/chrome_devtools_mcp_capabilities.json`.
6. `BrowserSession` now has a per-session MCP guard when `mcp.browser_backend: mcp` is configured.
7. Added `safe_io.py` for robust Windows long-path JSON/text/bytes writes.
8. Patched Data Map, Document Type, Rule, Transport Profile, BizFlow JSON writers to use safe writes.
9. Added `--runs-dir` to let the user output to a short path such as `C:/hip_runs`.
10. The replay manifest now records MCP required/used evidence.

## Safety status
Still no Save/Create/Submit/Delete/Deploy clicks are allowed in dummy-fill mode.

## Validation
- Python compile check passed for `hip_id_agent`.
- Targeted full dummy-fill unit tests: 6 passed.

## Recommended command

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --runs-dir "C:\hip_runs"
```
