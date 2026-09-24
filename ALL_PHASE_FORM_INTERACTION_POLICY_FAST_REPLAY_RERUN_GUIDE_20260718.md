# Rerun Guide — All-Phase Interaction Policy + Fast Replay

## Installation

Use the complete ZIP as the new baseline, or copy the hotfix files into the existing project while preserving the same relative paths.

## Command

Use the existing `run-full-dummy-fill` command. No new CLI flag is required.

## What to verify

At run root:

- `all_phase_form_interaction_policy.json`
- `all_phase_agentic_runtime_contract.json`

In each phase target execution JSON, verify:

```json
{
  "execution_profile": {
    "mode": "learning or validated_fast_replay"
  },
  "fast_replay_blueprint": {
    "eligible": true,
    "skip_known_branch_exploration": true
  }
}
```

For each dropdown/radio parent action, verify:

```json
{
  "transaction_proof": {
    "explicit_event_proof": {
      "pass": true,
      "requires_explicit_click": true
    },
    "conditional_child_visibility": {
      "pass": true
    }
  }
}
```

For every mutating action, verify:

- `interaction_preparation.bbox_stability.stable = true`
- target hit-test passes
- `protected_state_changes = []`
- no blocking validation
- exact value is stable

## Expected behavior

1. New or drifted forms run in learning mode.
2. Hidden fields trigger structural-parent analysis.
3. Parent dropdown/radio actions use explicit browser events.
4. Conditional children are checked before access.
5. Animated controls are not typed into until stable.
6. Once structure is strongly learned, later actions/runs use validated fast replay.
7. Drift or ambiguity automatically returns to learning mode.
8. No completed phase is reopened by reporting or judge-only failures.
