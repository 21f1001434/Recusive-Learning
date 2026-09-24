# HIP Portal Brain Run Guide

## One-time historical brain rebuild

Run this once after extracting the package into the project folder:

```powershell
cd "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg"

.\venv\Scripts\activate

python -m hip_id_agent.cli rebuild-portal-brain `
  --config .\config.yaml `
  --runs-dir ".\runs"
```

Check the memory:

```powershell
python -m hip_id_agent.cli portal-brain-status --config .\config.yaml
```

## Full deterministic run

```powershell
$RUNS_DIR = "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs"

python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --portal-brain `
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

Use `--rebuild-portal-brain` on the run command only when the historical brain must be recreated from all run folders.

## Existing Dell AIA environment

The text judge reuses:

```env
MODEL_NAME=gpt-oss-120b
BASE_URL=https://aia.gateway.dell.com/genai/dev/v1
CLIENT_ID=...
CLIENT_SECRET=...
DELL_AUTH_MODE=auto
USE_DELL_SSO=true
```

Configure a real multimodal model:

```env
HIP_VISION_MODEL=<Dell-AIA-vision-capable-model>
```

## Evidence generated in each run

```text
portal_brain_bootstrap.json
portal_brain_snapshot.json
deterministic_plans/
portal_form_knowledge/
section_judges/
full_dummy_fill_summary.json
```

The persistent brain remains under:

```text
data\hip_memory\portal_brain
```

Do not delete that directory between runs unless intentionally rebuilding the brain.
