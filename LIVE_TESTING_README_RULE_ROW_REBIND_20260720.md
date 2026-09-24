# Live Testing — Rules Row Rebind Baseline

Use this package as the baseline after run `UHAUL-POASN-FULL-DUMMY-20260720-191952`.

## Preserve learning

Before replacing the prior package, retain:

```text
data/hip_memory/portal_brain
```

Copy that directory into the same relative path in this package.

## Run

Use the same PowerShell command as the previous live run:

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

## Rules checkpoints to confirm

The new run should show:

1. Document Type Name (Version) committed before Conditions.
2. First physical Conditions row detected as count `1`.
3. Attribute Name/Unit `Receiver` selected from the popup owned by row 1.
4. Fresh row inspection verifies `Receiver` after Angular rerender.
5. Conditions `+` changes the count exactly from `1` to `2`.
6. Row 2 is filled with `Sender / Contains / DELL`.
7. Final proof contains two distinct row identities and exact live values.

## Fail-closed behavior

The agent stops without row reuse when:

- no physical Condition Type input can be found;
- the Attribute option is visible but is not committed in the fresh row;
- the first row is not exact;
- the Conditions `+` creates zero, two or more rows;
- two input rows resolve to the same physical identity.
