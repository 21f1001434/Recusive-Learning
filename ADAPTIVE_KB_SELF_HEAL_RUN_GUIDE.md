# Adaptive KB Self-Healing Run Guide

## Full safe run

```powershell
cd "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg"

.\venv\Scripts\activate

$RUNS_DIR = "C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs"

python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --portal-brain `
  --import-unified-kb `
  --require-unified-kb `
  --self-heal-kb `
  --revalidate-known-parent-branches `
  --kb-repair-min-confirmations 1 `
  --kb-supersede-min-confirmations 2 `
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

## Review KB repairs

```powershell
python -m hip_id_agent.cli kb-repair-status --config .\config.yaml
```

## Export the latest corrected KB manually

```powershell
python -m hip_id_agent.cli export-self-healed-kb `
  --config .\config.yaml `
  --output-dir ".\knowledge_base\self_healed"
```

## Default correction thresholds

- Missing canonical fact: one fully judged live confirmation.
- Canonical fact supersession: two fully judged explicit opposite confirmations.
- Failed/warning runs: evidence only; no effective KB change.
- Original reviewed KB: always preserved.
