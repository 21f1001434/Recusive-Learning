# Live Test — Rules Attribute Dependency Fix

This package includes every previous baseline fix plus the Rules Attribute Name/Unit dependency and popup-scope correction.

## Preserve validated memory

Before replacing the previous package, preserve:

```text
data\hip_memory\portal_brain
```

Copy it into the same location in the new extracted package.

## Run

Use the same command as the previous full dummy-fill run:

```powershell
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
  --vision-model "gemma-3-27b-it" `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "$RUNS_DIR"
```

## Expected Rules behavior

The Rules phase must visibly commit:

1. Document Type Name (Version): `XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)`
2. Execute Action(s) When: `one or more conditions are satisfied`
3. First condition: `Attributes / Equals / uhaul / Receiver`
4. Conditions `+`: one click producing exactly one new row
5. Second condition: `Attributes / Contains / DELL / Sender`

The agent must stop if Attribute Name/Unit still reports `No options found`; it must not click any background document-type checkbox.

The run remains no-save: Save/Create/Submit/Delete/Deploy actions are blocked.
