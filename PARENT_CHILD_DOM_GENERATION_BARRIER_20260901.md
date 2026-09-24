# v1.9.1 Parent/Child DOM-Generation Barrier

## Live failure boundary addressed
A browser action can be correct and a DDS parent value can be committed while Angular immediately replaces the parent/child DOM nodes. Reusing the selector captured before that replacement can make the next field look missing, click a stale element, or bind a child from the wrong generation.

## Runtime contract
1. AutoWebGLM remains the primary browser decision layer and emits/aligned-gates the next action.
2. The verified HIP tool adapter executes the action.
3. The parent value must reach exact stable committed state.
4. For every state-changing select/multi-select/radio/toggle, the runtime enters a post-commit DOM-generation barrier.
5. The barrier repeatedly recaptures only the parent plus its eligible conditional children. It requires the parent's exact committed value to survive and the value-free semantic/selector topology to match for N consecutive captures.
6. If Angular replaced the parent selector, the replacement is recorded and becomes the only selector eligible for subsequent verification.
7. Required conditional children must themselves be bindable with the same rebound selector topology for N consecutive captures before the next child action is allowed.
8. Before every graph node, controls are freshly recaptured. Cached controls are evidence only and can never be durable locators.
9. If the parent commit disappears after rerender or the generation never stabilizes within the bounded timeout, the transaction fails closed with `HIP_PARENT_COMMIT_LOST_AFTER_RERENDER` or `HIP_POST_COMMIT_DOM_GENERATION_NOT_STABLE`.

## Why this is safer than waiting for all DOM mutations to stop
HIP can continuously mutate passive loading/telemetry elements. The barrier therefore does not require a globally quiet DOM. It requires the *relevant parent/child binding topology* to stabilize while the committed value remains exact.

## Evidence added per transaction
`transaction_proof.post_commit_generation_barrier` records:
- stable/pass status
- samples and consecutive samples
- value-free generation digest
- selector before/after
- whether Angular replaced the selector
- exact parent binding proof
- value-free parent/child binding generation

`conditional_child_visibility` additionally records the stable child-binding digest and consecutive samples.

## Scope
The barrier is shared by Document Type and the generic state-graph executor used by Data Map, Rules, Transport Profiles and BizFlow. It applies to state-changing select/radio/toggle controls; text fields still use the normal exact/stable transaction verifier.
