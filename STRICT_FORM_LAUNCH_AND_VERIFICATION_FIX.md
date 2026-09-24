# Strict Form Launch and Verification Fix

## Verdict from run UHAUL-POASN-FULL-DUMMY-20260708-222143
The strict gate correctly rejected the run as not fully trusted. The manifest showed:

- Data Map: failed because no filled-form screenshot/fields were captured.
- Rule: failed because the after-fill evidence returned to listing/grid.
- Source/Target Transport Profile: failed because filled-form screenshots were not trusted by the gate.
- BizFlow: failed because it was still on the template picker and never launched the actual wizard tabs.

## Implemented fixes

1. **Data Map Add form detection fixed**
   - DDS keeps the listing grid in the background DOM while the Create Map drawer is open.
   - The old trust check rejected a valid Add form because it saw listing text behind the drawer.
   - New logic trusts foreground Create Map evidence: `Create Map`, `Map Reference`, `Mapping Details`, `Map Data`, `Browse Files`, etc.

2. **BizFlow template launch fixed**
   - The previous run clicked/recognized the B2B template card but did not launch the wizard.
   - The runner now prioritizes the **Outbound** button on the template card.
   - Safe template candidate logic now accepts `Outbound` / `Inbound` launch buttons.

3. **Strict surface detector improved**
   - It no longer merges `listing_before_add` snapshots with actual form snapshots.
   - It prefers `after_dummy_fill_no_save`, then `add_form_opened`, then other form snapshots.
   - This prevents false listing/template detection caused by background DOM text.

4. **Rule surface detection improved**
   - It now recognizes foreground Create Rule evidence such as `Create Rule`, `Rule :`, `Conditions :`, `Actions :`, and `Execute Action(s) When` even when the listing grid still exists behind the drawer.

5. **Screenshot discovery hardened**
   - Phase verification now scans after-fill, after-dummy, add-form-after, add-form, and any PNG fallback more robustly.

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

Trust only phases whose `strict_replication_gate.json` has `fatal: []`.
