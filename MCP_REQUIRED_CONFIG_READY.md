# MCP Required Config Update

This package has `config.yaml` already updated for the user's working Node/npx install:

```yaml
mcp:
  browser_backend: "mcp"
  chrome_devtools_command: 'C:\Program Files\nodejs\npx.cmd'
  chrome_devtools_startup_timeout_seconds: 60
  chrome_devtools_mcp_direct_backend_enabled: true

reporting:
  runs_dir: 'C:\hip_runs'
```

Use `C:\hip_runs` to avoid OneDrive/deep-path write issues.

Run MCP check:

```powershell
python -m hip_id_agent.cli chrome-devtools-check `
  --config .\config.yaml `
  --run-dir "C:\hip_runs\mcp-check"
```

Run full dummy fill with MCP required:

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
