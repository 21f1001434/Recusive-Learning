# Live Run — Autonomous HIP Mission

## Recommended command

From the extracted project root:

```powershell
.\RUN_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs"
```

For the largest forensic evidence bundle:

```powershell
.\RUN_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -WriteHeavyEvidence
```

Direct Python command:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config ".\config.yaml" `
  --customer "UHAUL-POASN-AUTONOMOUS-MISSION" `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
  --autonomous-mission `
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
  --forensic-evidence `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "C:\hip_runs"
```

## Expected Rule progression after this fix

1. Rule Name
2. Document Type Name (Version)
3. Description
4. Default Action Name
5. Action Type = `Route Document`
6. Mapping Identifier = `DELLCoXMLASNXX08C_U-HAUL(1.0)`
7. First Condition = `Attributes / Equals / uhaul / Receiver`
8. Conditions `+` creates exactly one distinct second row
9. Second Condition = `Attributes / Contains / DELL / Sender`
10. Exact-state, text and vision judges pass
11. Mission proceeds to Source Transport Profile, Target Transport Profile and BizFlow

## Evidence to inspect

The Mapping Identifier attempt should now contain one of these success markers:

```text
commit_recovered_from = post_click_state
commit_recovered_from = pre_click_selected_state
commit_recovered_from = post_dispatch_state
commit_recovered_from = post_enter_state
commit_recovered_from = completed_owned_option_click
```

The first marker is expected for the `015927` live behavior.

Preserve your existing validated memory:

```text
data\hip_memory\portal_brain
```
