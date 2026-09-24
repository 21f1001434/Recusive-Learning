# Live testing — Rules global-validity gate before Conditions +

## Baseline

Use `HIP_PORTAL_AGENTQ_RULE_GLOBAL_VALIDITY_LIVE_READY_434_TESTS_20260720.zip`.

Preserve before replacing code:

```text
data/hip_memory/portal_brain
```

## Run

Use the same PowerShell command as the previous UHAUL full-dummy run.

## Expected Rules order

```text
Open Create Rule
  -> Rule Name
  -> Document Type Name (Version)
  -> Description
  -> Default Action Name
  -> Default Action Type
  -> Mapping Identifier Name (Version)
  -> Execute Action(s) When
  -> Condition row 1: Attributes / Equals / uhaul / Receiver
  -> prove foreground Create Rule form is valid
  -> click Create Condition
  -> prove exact FormArray transition 1 -> 2
  -> Condition row 2: Attributes / Contains / DELL / Sender
  -> final one-to-one and exact-value verification
```

## Evidence to inspect

```text
rule/rule_kb/rule_repeatable_row_audit.json
rule/rule_kb/rule_target_branch_execution.json
rule/phase_execution_attempts.json
runtime_self_heal/runtime_self_heal_summary.json
```

In the Conditions audit confirm:

- `prerequisite_result.pass` is `true`
- required Action fields are `filled: true`
- `form_validity_before_click.valid` is `true` or not positively invalid
- `physical_click_attempts[*].click_probe.clicked` is `true`
- `exact_plus_one` is `true`
- final row count is `2`
- row 1 remains `Receiver / Equals / uhaul`
- row 2 is `Sender / Contains / DELL`

## Local verification

```powershell
python -m compileall -q hip_id_agent tests
python -m pytest -q
```

Expected: `434 passed`.
