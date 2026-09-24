# V243R9 — Phase Commit / Handoff Final

## Live defect fixed
A completed Data Map could keep cycling because the autonomous runtime treated a newly synthesized semantic node as unfinished learning even after the node had been filled and exactly verified. That produced `retry_required` and eventually `goal not proven before bounded adaptive/no-progress guard`.

R9 changes the contract:

1. Runtime-synthesized semantic nodes are valid learned controls. They do not block completion once every input-owned node is exact and authoritative.
2. `Looks correct` is never silently rewritten into `Needs correction`. If the earlier exact checkpoint is stale, human approval enters `pass_pending_live_reproof`; the controller performs one read-only evidence rebuild and checkpoint refresh without refilling the form.
3. After exact + judge + human acceptance, `phase_acceptance_commit.json` and `phase_completion_token.json` are written. The token contains `reexecute_same_phase=false` and the transition coordinator immediately hands the same browser to the next executable phase.
4. Learning/observability enrichment is no longer allowed to reopen an exact+judge accepted phase. Incomplete learning evidence becomes a warning and simply prevents deterministic-memory promotion.

## Expected Data Map behavior

Authentication → Data Map → fill/upload/select → exact live proof → judge reconciliation → human review (when learning) → completion token → Source Document Type.

There must be no second Data Map execution after a valid completion token.

## In-place upgrade
Use `HIP_PORTAL_V243R9_VERIFIED_IN_PLACE_OVERWRITE.zip` when the existing project folder must be preserved. The updater backs up replaced files and preserves `.env`, `config.yaml`, `input.json`, `runs`, `data/hip_memory`, `.backend_runtime`, and `.hip_runtime`.
