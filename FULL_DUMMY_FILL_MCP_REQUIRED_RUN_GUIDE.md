# Run Guide — Full Dummy Fill with MCP Required

## 1. Verify MCP first

```powershell
python -m hip_id_agent.cli chrome-devtools-check `
  --config .\config.yaml `
  --run-dir "C:\hip_runs\mcp-check"
```

Expected: `available: true` and required tools such as navigation, screenshot, click/fill/press, DOM snapshot and network list.

## 2. Run full dummy-fill E2E

Use a short output folder to avoid Windows OneDrive MAX_PATH failures:

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

## 3. What it will generate

```text
mcp_preflight/chrome_devtools_mcp_capabilities.json
FULL_DUMMY_FILL_E2E_REPORT.html
full_dummy_fill_summary.json
phase_verification_report.csv
vision_verification_prompts.json
vision_verification_results.json
flash_fill_replay_manifest.json
FAST_FILL_AGENT_PLAYBOOK.md
fast_replay_blueprints/*.json
UPLOAD_FULL_DUMMY_FILL_E2E_SUMMARY.zip
```

## 4. Behavior

- MCP is required.
- If MCP is unavailable, the run stops before form fill.
- No silent pure-Playwright fallback in `--require-mcp` mode.
- No Save/Create/Submit/Delete/Deploy click in dummy-fill mode.
