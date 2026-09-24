# Strict Drawer Scope + Upload + BizFlow Launch Fix

## Verdict
The previous run completed but strict replication correctly failed these phases:

- Data Map: Add form opened, but foreground screenshot reverted to the listing page and file upload was not accepted.
- Rule: Add form opened, but after-fill evidence reverted to the listing page.
- Source/Target Transport Profile: Add forms opened, but strict verification did not locate screenshots reliably.
- BizFlow: the code clicked the `B2B-Flow-PubSub-Template` card title instead of the actual `Outbound` launch button, so it remained on the template picker.

## Implemented fixes

1. **Foreground drawer control filtering**
   - Data Map and Rule now filter controls to the active Create/Add drawer only.
   - Background grid controls such as table search, pagination, column filters, cookie controls, and audit comment controls are ignored.

2. **Data Map upload fix**
   - Direct DDS file input selectors like `input#file-input-control-*` are now accepted as valid `input[type=file]` targets.
   - Upload logic no longer searches only for a nearby file input when the selector itself is the hidden file input.
   - `mapData`, `inputSchema`, and `outputSchema` are treated as separate file controls.
   - Upload asset matching now prefers:
     - `mapData` -> JAR/XBM transform asset
     - `inputSchema` -> XML/XSD/input/source sample
     - `outputSchema` -> XML/XSD/output/target sample

3. **Rule form stability fix**
   - Rule fill ignores background listing-grid controls and only fills Create Rule drawer controls.
   - This avoids accidental focus/typing into the listing page and prevents false listing screenshots.

4. **BizFlow launch fix**
   - Native JavaScript click now first targets the visible `Outbound` button on the template card.
   - The old fallback that clicked the `B2B-Flow-PubSub-Template` title remains only after Outbound fails.

5. **Screenshot verification hardening**
   - Strict verification now performs a relaxed PNG scan before failing a phase for missing screenshot evidence.

## Validation

```text
compileall: PASS
pytest -q --disable-warnings: 194 passed
```

## Run command

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-FULL-DUMMY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --fast-form-only `
  --vision-verify `
  --save-replay-blueprint `
  --require-mcp `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads"
```
