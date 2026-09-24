# Run Guide — MCP Required + Input-driven Repeatable Rows

Run from the project root:

```powershell
python -m hip_id_agent.cli chrome-devtools-check `
  --config .\config.yaml `
  --run-dir "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs\mcp-check"
```

Then run the fill/learn/replicate flow:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN"
```

No `--runs-dir` is required because `config.yaml` already points to the requested `runs` folder. If you want to override it temporarily, pass `--runs-dir` manually.
