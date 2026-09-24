# Adaptive HIP KB Self-Healing — Implementation Verdict

## Verdict

Implemented. The reviewed Unified KB is no longer treated as immutable truth.
It is now a strong canonical seed that is continuously revalidated against the
live HIP Portal through official Playwright MCP exploration.

The original KB files are never overwritten. Judge-approved corrections are
stored in the persistent Portal Brain and exported as versioned self-healed KB
and Knowledge Graph files.

## Runtime behavior

1. Import the reviewed Unified KB and KG.
2. Build the initial deterministic plan from the persistent brain and input.json.
3. Open the exact HIP form through the dual-MCP browser runtime.
4. Revalidate current and known structural parent-value branches.
5. Capture before/after DOM state, Playwright MCP accessibility evidence and screenshots.
6. Apply the live exploration dependency overlay to the current in-memory plan.
7. Fill the current section deterministically.
8. Require deterministic DOM, gpt-oss-120b text judge and vision judge approval.
9. Reconcile live evidence with canonical KB facts.
10. Add missing facts after the configured validated confirmation count.
11. Supersede a canonical fact only after repeated explicit opposite live evidence.
12. Export a versioned self-healed KB while preserving the original source KB.

## Safety and trust model

- A warning, failed or unjudged run cannot modify effective KB truth.
- Missing live facts can be added after a fully validated section/phase.
- Canonical facts are not deleted on first disagreement.
- A canonical dependency is superseded only after repeated validated explicit
  opposite evidence; default is two confirmations.
- Every repair contains source run, judge proof, evidence, timestamps and reason.
- Save/Create/Submit/Delete/Deploy remain blocked.

## New persistent memory structures

`data/hip_memory/portal_brain/kb_repairs/repair_index.json`

Per-phase brain files now contain:

- `kb_repairs`
- `validated_field_overrides`
- `superseded_canonical_edges`
- `kb_repair_history`
- `kb_revision`

## New run outputs

Each completed run exports:

- `self_healed_kb/HIP_Unified_Deep_KB.self_healed.json`
- `self_healed_kb/HIP_Unified_Knowledge_Graph.self_healed.json`
- `self_healed_kb/self_healed_kb_manifest.json`

## Same-run correction

Live exploration now patches the current in-memory deterministic plan before
field filling. Newly observed parent-child dependencies can therefore affect the
same phase execution. Persistent promotion still waits for the full judge gate.

## Validation

- Python compilation: passed
- CLI discovery/help: passed
- Unified KB import/export smoke test: passed
- Automated tests: 231 passed

A live authenticated Dell HIP Portal run is still required to validate actual
portal behavior. Local tests do not claim a live portal pass.
