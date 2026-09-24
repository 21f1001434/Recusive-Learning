# Patched HIP Portal Live No-Save Run Guide

## Required environment

Keep the existing Dell AIA text settings and configure a real multimodal deployment separately:

```powershell
$env:MODEL_NAME = "gpt-oss-120b"
$env:HIP_VISION_MODEL = "<real Dell AIA multimodal deployment>"
$env:HIP_SKIP_VISION_CAPABILITY_PROBE = "false"
```

Do not set `HIP_VISION_MODEL` to `gpt-oss-120b` unless that exact Dell deployment is documented and verified as multimodal. The run now performs an image capability probe before opening HIP.

## Command

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

## New fail-fast files

Inspect these first:

- `input_contract_preflight.json`
- `portal_brain_policy_preflight.json`
- `vision_model_preflight.json`
- each phase `mcp_runtime/dual_mcp_same_surface.json`
- each phase `section_judge_gate.json`

## Expected U-HAUL corrections

- Source TP deployment group: `dce-shared-sender`
- Target TP deployment group: `dce-shared-receiver`

## Safety

The patch blocks final mutation words in two layers:

1. Python pre-click guard for BrowserSession actions.
2. Browser capture-phase click interceptor for direct Playwright and MCP-originated DOM clicks.

The specialized `Create Biz Flow` action-menu launcher remains allowed; final form Create remains blocked.

## Acceptance

Do not call the run successful unless all seven phases are present in `phase_verification_report.csv`, every section judge passes, exact row counts match, the same-surface MCP gate passes, and no safety-blocked or unsafe mutation appears.
