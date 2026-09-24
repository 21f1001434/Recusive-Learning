# Rerun Guide — Agentic Runtime Self-Heal

Use the same full command. Runtime self-healing is enabled by default.

Optional explicit flags:

```powershell
--runtime-self-heal `
--runtime-self-heal-max-attempts 5 `
--runtime-self-heal-max-total-repairs 20
```

Expected console behavior after a recoverable error:

```text
failure captured
→ classified
→ safe repair executed
→ interrupted phase replayed in the same authenticated Chrome context
→ section judge rerun
→ next phase begins only after pass
```

Inspect:

```powershell
$LATEST_RUN = Get-ChildItem $RUNS_DIR -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content "$($LATEST_RUN.FullName)\runtime_self_heal\runtime_self_heal_summary.json"
Get-ChildItem "$($LATEST_RUN.FullName)\runtime_self_heal" -Recurse
Get-ChildItem "$($LATEST_RUN.FullName)" -Recurse -Filter "phase_execution_attempts.json"
```

A recovered phase should contain `self_heal_resolution.json`, and its final `section_judge_gate.json` must show `pass: true`.

The loop stops when:

- the same failure signature repeats beyond the configured bound;
- the total repair budget is exhausted;
- a repair action fails;
- a mutation-safety violation is detected;
- the independent judge still fails after all allowed attempts.
