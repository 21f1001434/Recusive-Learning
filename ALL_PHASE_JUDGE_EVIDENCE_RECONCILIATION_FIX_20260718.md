# All-Phase Judge Evidence Reconciliation Fix — 2026-07-18

## Supplied run

Run: `UHAUL-POASN-FULL-DUMMY-20260718-153150`

The run completed Data Map and then completed the Source Document Type form exactly once. The authoritative Source Document Type execution contained 29 successful actions, no failed action, a valid filled-form screenshot, one Document Identifier row, five Attribute rows, and exact field-bound DOM evidence.

The phase was incorrectly blocked because the verification summary had status `pass_with_warnings`. The only warning was incomplete dropdown-option enrichment. `section_judge.py` required the status to equal the literal string `pass`, so it changed the deterministic judge to false even though all exact state checks passed.

That false deterministic result prevented the existing text and vision contradiction reconciler from promoting unsupported model observations to non-blocking audit findings.

## Contradictions in the supplied run

The text judge reported:

- Version expected `1`, observed `1.0`.
- Document Identifier Operation differed only in capitalization.

Exact DOM evidence proved:

- Numeric version equivalence: `1 == 1.0`.
- Operation was exactly `All conditions are satisfied`.

The vision judge reported a different spelling for `attributes[1].expression`. Exact row-scoped DOM evidence proved the input value was present exactly, including the source input spelling `PartnerInfomation`.

## Fix

A successful verification status is now either:

- `pass`
- `pass_with_warnings`

`failed` remains fail-closed.

Warnings remain visible in the final evidence, but an advisory warning cannot make deterministic exact state false. Fatal conditions are already represented separately as `failed`, including:

- missing screenshots
- lost active form surface
- unsafe clicks
- failed exact fill actions
- blocking validation
- missing form controls
- dummy-value leakage
- upload failure

Once deterministic exact state remains true, unsupported GPT and vision contradictions are reconciled using canonical field-bound DOM evidence. Genuine missing values, row mismatches, failed actions, blocking validation, and concrete visual issues without exact DOM proof remain blocking.

## Exact supplied-run replay

The supplied artifacts were replayed through the patched judge:

- Verification: `pass_with_warnings`
- Deterministic judge: pass
- Text judge: reconciled, pass
- Unsupported text contradictions: 2
- Vision judge: reconciled, pass
- Unsupported vision contradictions: 1
- Overall Source Document Type gate: pass
- Browser replay required: no
- Form mutation required: no
- Next phase: Target Document Type

See `RUN_153150_EXACT_JUDGE_REPLAY.json`.

## Scope

The change is in the shared artifact section judge and therefore applies to every phase and every HIP form family using the shared phase verification gate.

## Validation

- Python compilation: passed
- Focused reconciliation regressions: passed
- Complete suite: 400/400 passed
- Exact supplied-run replay: passed
- Authenticated live HIP rerun: not performed in this environment
