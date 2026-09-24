# Unified KB + Persistent Brain Run Guide

## 1. Import the reviewed KB

Run once after extracting the package:

```powershell
python -m hip_id_agent.cli import-unified-kb `
  --config .\config.yaml `
  --kb-path ".\knowledge_base\HIP_Unified_Deep_KB.json" `
  --graph-path ".\knowledge_base\HIP_Unified_Knowledge_Graph.json"
```

Check status:

```powershell
python -m hip_id_agent.cli unified-kb-status --config .\config.yaml
python -m hip_id_agent.cli portal-brain-status --config .\config.yaml
```

## 2. Rebuild from historical runs and re-seed canonical KB

```powershell
python -m hip_id_agent.cli rebuild-portal-brain `
  --config .\config.yaml `
  --runs-dir ".\runs"
```

## 3. Full deterministic run

```powershell
$RUNS_DIR = "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs"

python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --portal-brain `
  --import-unified-kb `
  --require-unified-kb `
  --exploration-agent `
  --explore-parent-branches `
  --exploration-max-values-per-parent 30 `
  --section-judge `
  --require-text-judge `
  --require-vision-judge `
  --section-judge-max-repairs 2 `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "$RUNS_DIR"
```

## Required vision configuration

`gpt-oss-120b` remains the text/state judge. Configure an actual multimodal Dell AIA model separately:

```env
HIP_VISION_MODEL=<Dell-AIA-vision-capable-model>
```

The run blocks when the vision judge is required but unavailable.
