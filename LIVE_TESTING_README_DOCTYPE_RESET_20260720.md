# HIP Agent Live Testing — Document Type Reset Build

## Package purpose

This build fixes the Document Type structure-learning reset failure observed in run:

```text
UHAUL-POASN-FULL-DUMMY-20260720-163756
```

It includes all prior Rules Conditions and FormArray fixes.

## Preserve validated memory

Before replacing the previous baseline, preserve:

```text
data\hip_memory\portal_brain
```

Copy that directory into the same relative path in the new extracted package.

## Run command

Use the same PowerShell command as the previous live run:

```powershell
$RUNS_DIR = ".\runs"

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

## Expected Source Document Type sequence

The phase should now show this progression:

```text
capture_add_form
structure probe / parent branch learning
safe discard of the structure-probe drawer
Document Types listing toolbar restored
clean + Add drawer reopened
repeatable rows created from input.json
fill_target_branch 29/29
exact form frozen and verified
```

The reset evidence is written under the phase `doctype_kb` directory using names such as:

```text
doctype_surface_structure_learned_clean_target_form_discarded.json
doctype_surface_structure_learned_clean_target_form_reopened.json
```

## Fail-closed conditions

The phase stops before entering customer values when:

- The active Create Document Type drawer cannot be safely closed.
- The listing `+ Add` toolbar cannot be proven visible.
- A safe discard confirmation cannot be identified.
- Reopening does not produce a valid Create Document Type surface.
- The exact repeatable-row count cannot be created.

## Local validation

```powershell
python -m compileall -q hip_id_agent tests
pytest -q
```

Expected result:

```text
422 passed
```
