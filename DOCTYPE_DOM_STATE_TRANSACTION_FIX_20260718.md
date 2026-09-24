# Document Type DOM-State Transaction Intelligence Fix

## Problem

The runtime captured DOM, HTML, CSS-related visibility, DOM events and screenshots, but control execution still depended heavily on label/section/row scoring. On a dynamic Angular + Dell DDS form, generated IDs and repeated labels can change after every selection. A highest-score resolver can therefore bind a graph node to the wrong physical control, especially in repeated Attribute rows.

The agent also verified the intended field after an action but did not prove that already-committed fields remained unchanged. This allowed a later DDS interaction or fallback to corrupt Version or an earlier row without being identified as the causal action.

## Implemented model

### 1. Rich live control fingerprint

Each Document Type control now captures:

- semantic field key
- section, repeatable row kind and zero-based data-row index
- Angular `formcontrolname` / `ng-reflect-name`
- framework key and DDS/custom component tag
- accessibility role, label, placeholder and ARIA relationships
- semantic DOM path that excludes generated IDs
- selection mode, selected values and selected count
- disabled/read-only/invalid state
- hit-tested interactability using `document.elementFromPoint`
- pointer-events, z-index and bounding box

Dynamic CSS selectors remain current-action evidence only.

### 2. One-to-one form-state model

The deterministic input graph is bound to the live form through `hip.doctype-form-state-model.v2`.

Every node receives:

- candidate controls
- best and second-best scores
- confidence margin
- selected durable identity
- resolved, deferred, missing or ambiguous status

The model fails closed when:

- two physical controls are plausible within the confidence margin
- two graph nodes bind to the same control
- a required final control is missing
- the final graph-to-form mapping is not one-to-one

### 3. Transactional field execution

For every field action the runtime now:

1. captures the live form
2. resolves one unique target control
3. snapshots all previously committed graph nodes
4. performs exactly one allowed action
5. waits for the Angular/DDS form to reach two consecutive stable samples
6. verifies the intended exact value
7. compares every protected committed node before and after
8. rejects the action if an unrelated field changed

Unintended changes raise `HIP_DOCTYPE_UNINTENDED_MUTATION` and are not converted into success by GPT, vision or a generic fallback.

### 4. Version protection

Document Type Version remains `verify_only`. It is never typed into. After it is verified, it becomes a protected committed node. Any later action changing Version is attributed to that action and blocks the phase immediately.

### 5. Portal Brain learning

The Portal Brain now receives:

- initial and final form-state models
- framework/component metadata
- binding confidence and candidate evidence
- per-node transaction proofs
- stable form-state digests
- protected-state changes
- DOM event and mutation contracts

Knowledge promotion remains gated by deterministic, GPT-OSS-120B and required Gemma judges.

## New artifacts

Per Document Type phase:

- `doctype_kb/doctype_form_state_model.json`
- enriched `doctype_kb/doctype_target_branch_execution.json`
- enriched `doctype_kb/doctype_filled_form_evidence_lock.json`

## Safety

- no Save/Create/Submit/Delete/Deploy/Publish action added
- ambiguous controls are never guessed
- generated DDS IDs are never durable identity
- GPT and vision cannot override exact control binding or mutation checks
- learning/reporting remains non-mutating after exact fill

## Verification

- compilation: passed
- new focused tests: 6 passed
- full suite: 367 passed
- authenticated Dell HIP live rerun: not performed in this environment
