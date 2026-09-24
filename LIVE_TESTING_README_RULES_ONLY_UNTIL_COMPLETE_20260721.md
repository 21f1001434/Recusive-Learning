# Live Rules-only Until-Complete Run

## Preserve memory

Before replacing an older package, retain:

```text
data\hip_memory\portal_brain
```

Copy that directory into the same path in the new package.

## Recommended PowerShell command

```powershell
.\RUN_RULES_UNTIL_COMPLETE.ps1 `
  -RunsDir "C:\hip_runs"
```

Equivalent direct command:

```powershell
python -m hip_id_agent.cli run-full-dummy-fill `
  --config .\config.yaml `
  --customer UHAUL-POASN-RULES-ONLY `
  --input-json ".\examples\uhaul_poasn_full_dummy_input.json" `
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
  --vision-model "gemma-3-27b-it" `
  --save-replay-blueprint `
  --require-mcp `
  --runtime-self-heal-until-complete `
  --golden-screenshot-dir ".\golden_screenshots\UHAUL-POASN" `
  --upload-assets-dir ".\uploads" `
  --runs-dir "C:\hip_runs"
```

## Expected console message

```text
Rules-only mode: opens only Create Rule...
Runtime self-heal until-complete enabled: no attempt/signature/repair-count limit...
```

## Completion contract

The loop is complete only after all of these are proven:

- Rule Name equals `DELLCoXMLASNXX08C_U-HAUL_RULE`
- Document Type equals `XML_DellAutoASN_10_U-HAUL_ANS_IB(1.0)`
- Action Type equals `Route Document`
- Mapping Identifier equals `DELLCoXMLASNXX08C_U-HAUL(1.0)`
- Conditions row 1 equals `Attributes / Equals / uhaul / Receiver`
- Conditions row 2 equals `Attributes / Contains / DELL / Sender`
- The final screenshot is accepted against `Rules.png`
- Deterministic, text and vision judges pass

## Safety

The until-complete mode has no retry-count limit, but it does not weaken the portal safety policy. It cannot click Save, Create, Submit, Delete, Deploy, Publish, Update, Confirm, Remove, Enable or Disable. A proven unsafe/mutating request remains a hard blocker. Stop the process manually with `Ctrl+C` when the browser or network environment is intentionally unavailable.
