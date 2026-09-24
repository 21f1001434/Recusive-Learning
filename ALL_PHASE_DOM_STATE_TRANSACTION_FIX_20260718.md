# All-Phase DOM State Transaction Fix — 2026-07-18

## Objective

Apply the semantic DOM/Angular/DDS control-binding and transaction safeguards to every HIP phase, not only Document Type:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

## Root architectural gap

The portal already captured DOM events, HTML, network evidence, CSS state and JavaScript mutations. However, non-Document-Type phases still used the older generic resolver, which selected the highest-scoring control without requiring a confidence margin. Repairs also did not prove that previously completed fields remained unchanged.

This could allow:

- repeated labels to bind to the wrong Rule/Process Step row;
- Source and Target BizFlow controls with the same label to be confused;
- a Transport Profile parent selection to rerender and silently alter an earlier field;
- a judge disagreement to replay a phase whose exact execution had already passed.

## Shared all-phase runtime

The shared `execute_phase_state_graph()` engine now performs:

```text
capture live semantic form model
→ bind input node to one unique control
→ reject ambiguous score margins
→ snapshot all committed controls
→ execute one safe action
→ wait for two stable Angular/DDS observations
→ verify exact expected value
→ compare committed controls before/after
→ fail on unintended mutation
→ commit node
```

## Durable control identity

A control identity is built from:

- section/tab/fieldset;
- repeatable row kind and row index;
- semantic field key;
- `formcontrolname`, `ng-reflect-name`, or stable name;
- DDS/custom component tag;
- accessibility role, label and placeholder.

Generated DDS IDs and CSS paths are current-action evidence only.

## Confidence-bound resolution

The resolver records:

- best candidate score;
- second candidate score;
- score margin;
- selected durable identity;
- up to five competing candidates.

Default rules:

- minimum score: 70;
- minimum confidence margin: 14;
- ambiguous candidates are rejected;
- no "best guess" fill is allowed.

## Transaction proof

Each state-graph attempt now contains:

```json
{
  "binding_diagnostics": {
    "resolved": true,
    "best_score": 260,
    "second_score": 145,
    "score_margin": 115
  },
  "transaction_proof": {
    "stability": {
      "stable": true,
      "consecutive_samples": 2
    },
    "protected_state_changes": []
  }
}
```

A changed committed value fails with:

```text
HIP_PHASE_UNINTENDED_MUTATION
```

An ambiguous target fails with:

```text
HIP_PHASE_AMBIGUOUS_CONTROL_BINDING
```

A non-one-to-one final form model fails with:

```text
HIP_PHASE_FORM_MODEL_NOT_ONE_TO_ONE
```

## All-phase exact-state lock

After a phase's authoritative execution artifact passes, the orchestrator writes:

```text
<phase>/phase_exact_state_lock.json
```

The lock states that:

- the form is frozen;
- phase replay is not allowed;
- judges may review but cannot mutate;
- an evidence disagreement receives one read-only rebuild/rejudge;
- an unresolved disagreement stops fail-closed rather than refilling the phase.

This policy now applies to every phase, not only Source/Target Document Type.

## Portal Brain learning

The existing target-branch knowledge export now receives for all phases:

- initial and final form-state models;
- one-to-one binding results;
- confidence margins;
- transaction proofs;
- committed-node history;
- observed parent-child mutation edges;
- durable framework/component metadata.

Promotion remains gated by deterministic, GPT-OSS-120B and Gemma judges.

## Safety

The change does not permit final mutation actions. Save/Create/Submit/Delete/Deploy/Publish/Update/Confirm remain blocked.

## Validation

- Python compilation: PASS
- New all-phase transaction tests: PASS
- Complete source suite: 373/373 PASS
- Authenticated Dell HIP live rerun: not performed in this environment
