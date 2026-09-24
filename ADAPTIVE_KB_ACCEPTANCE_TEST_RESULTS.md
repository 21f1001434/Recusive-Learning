# Adaptive KB Acceptance Test Results

Command:

```text
pytest -q
```

Result:

```text
231 passed in 8.71s
```

Additional checks:

```text
Python compilation: PASS
CLI kb-repair-status command: PASS
CLI export-self-healed-kb command: PASS
run-full-dummy-fill self-heal flags: PASS
Unified KB import smoke test: PASS
Self-healed KB export smoke test: PASS
Original KB preservation test: PASS
Same-run live plan overlay test: PASS
Repeated-conflict supersession test: PASS
Candidate-run non-promotion test: PASS
```
