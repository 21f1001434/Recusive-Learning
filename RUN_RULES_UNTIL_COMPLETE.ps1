param(
    [string]$Config = ".\config.yaml",
    [string]$InputJson = ".\examples\uhaul_poasn_full_dummy_input.json",
    [string]$RunsDir = ".\runs",
    [string]$VisionModel = "gemma-3-27b-it",
    [string]$GoldenDir = ".\golden_screenshots\UHAUL-POASN",
    [string]$UploadAssetsDir = ".\uploads"
)

$ErrorActionPreference = "Stop"

python -m hip_id_agent.cli run-full-dummy-fill `
  --config $Config `
  --customer UHAUL-POASN-RULES-ONLY `
  --input-json $InputJson `
  --rules-only `
  --fast-form-only `
  --portal-brain `
  --import-unified-kb `
  --require-unified-kb `
  --self-heal-kb `
  --revalidate-known-parent-branches `
  --kb-repair-min-confirmations 10 `
  --kb-supersede-min-confirmations 10 `
  --exploration-agent `
  --explore-parent-branches `
  --exploration-max-values-per-parent 100 `
  --section-judge `
  --require-text-judge `
  --require-vision-judge `
  --section-judge-max-repairs 5 `
  --vision-verify `
  --vision-model $VisionModel `
  --save-replay-blueprint `
  --require-mcp `
  --runtime-self-heal-until-complete `
  --golden-screenshot-dir $GoldenDir `
  --upload-assets-dir $UploadAssetsDir `
  --runs-dir $RunsDir
