# HIP live testing — Rules FormArray corrected baseline

Preserve the existing validated memory directory before replacing the previous package:

```text
data\hip_memory\portal_brain
```

Run with the same command used for the 05:40:50 test. The corrected Rules phase now requires two distinct physical Condition rows before proceeding.

Important evidence written during Rules execution:

- `rule/rule_kb/rule_target_branch_execution.json`
- repeatable Conditions audit in the Rule KB output
- final form state model with unique row identities

Expected Rules Conditions:

1. Receiver / Equals / uhaul
2. Sender / Contains / DELL

The run remains no-save: Save/Create/Submit/Delete/Deploy are blocked.
