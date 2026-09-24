# V243R11 — Interactive Teaching + Immediate Deterministic Promotion

## Purpose
V243R11 fixes the remaining gap between a human-approved, exact-verified HIP phase and deterministic reuse.

### Atomic phase graduation
A phase now graduates when all three are true:
1. live exact reproof passes,
2. judge is reconciled/passes,
3. the human chooses **Looks correct**.

At that point the phase writes:
- `phase_acceptance_commit.json`
- `phase_deterministic_promotion.json`
- `phase_completion_token.json`

The accepted phase must hand off to the next executable phase. Later API/observability/learning enrichment is warning-only and cannot reopen the accepted form.

## Interactive teaching
Control Center now supports a real demonstration workflow:
1. **Start teaching**
2. navigate/click through the live HIP portal yourself
3. **Finish & learn**
4. leave the live form correct
5. **Looks correct**

The borrowed persistent BrowserSession already records DOM clicks/navigation. R11 compiles only semantic structure into teaching memory:
- route/path transitions,
- visible action labels,
- roles/tags,
- semantic order.

It does **not** promote:
- customer values,
- raw CSS/XPath selectors,
- screen coordinates.

A demonstration becomes trusted only after exact live reproof + judge pass + human confirmation.

## Deterministic replay
Human-supervised exact success is sufficient for immediate deterministic validation. A second autonomous run is no longer required for that phase. Every replay still requires live semantic resolution and exact reproof; portal drift falls back to adaptive exploration.

## Learning behavior
Failed/blocked attempts remain negative/candidate learning. Only exact+judge+human accepted trajectories are trusted.
