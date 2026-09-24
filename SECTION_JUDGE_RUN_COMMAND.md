# Run with strict section judges

PowerShell:

```powershell
cd "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg"

.\venv\Scripts\activate

$RUNS_DIR = "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs"

python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --section-judge `
  --require-text-judge `
  --require-vision-judge `
  --section-judge-max-repairs 2 `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "$RUNS_DIR"
```

The existing Dell AIA `.env` is reused for text judging. Add a real multimodal Dell AIA deployment name:

```env
HIP_VISION_MODEL=<Dell-AIA-vision-capable-model>
```

The code also accepts `AIA_VISION_MODEL`, `VISION_MODEL_NAME`, or `VISION_MODEL`.
