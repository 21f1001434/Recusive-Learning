# Playwright MCP Dual-MCP Run Guide

## 1. Install/update local dependencies

```powershell
cd "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg"
.\venv\Scripts\activate
pip install -e .
```

Node.js and `npx` must be available. The configured Windows command is:

```text
C:\Program Files\nodejs\npx.cmd
```

## 2. Validate both MCP servers

```powershell
python -m hip_id_agent.cli dual-mcp-check `
  --config .\config.yaml `
  --run-dir ".\runs\dual-mcp-check"
```

The result must show:

```text
available: true
mode: dual-mcp
playwright_mcp.available: true
chrome_devtools_mcp.available: true
```

## 3. Run the full deterministic no-save fill

```powershell
$RUNS_DIR = "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs"

python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "$RUNS_DIR"
```

`--require-mcp` now requires both official Playwright MCP and Chrome DevTools MCP. The run stops when either required MCP cannot start.

## 4. Confirm plan source

Open:

```text
<run>\deterministic_plans\deterministic_plan_manifest.json
```

Each phase must show the previous learned blueprint/KB evidence as its knowledge source. The generated action plan should say:

```text
plan_source: HIP Portal learned form knowledge merged with current input.json
primary_executor: official @playwright/mcp attached through CDP
```
