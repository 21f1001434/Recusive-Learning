# All HIP Phases Until-Complete Forensic Self-Heal

## Scope

The progress-driven exploration/exploitation runtime now applies to the complete HIP form sequence:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

The new `--all-phases-until-complete` profile forces the complete sequence and enables unlimited safe retries for every phase. It cannot be combined with `--rules-only` or a partial phase list.

## Recovery policy

Each phase repeats until all of the following pass:

- Deterministic exact-state verification
- One-to-one field/control binding verification
- Repeatable-row count and identity verification
- Required upload/dropdown/multi-select verification
- Dell AIA text judge
- Dell AIA vision judge
- Golden-image state comparison

The loop has no attempt, repeated-signature, or total-repair limit in this profile. It alternates:

- **Exploration:** recapture the current portal state, inspect conditional controls, overlays, API traffic and golden-image differences, and request a safe Dell AIA recovery recommendation.
- **Exploitation:** replay the latest judge-approved selector, interaction, dependency and wait trajectory using values from the current `input.json`.

Unsafe final portal mutations remain permanently blocked.

## Expanded failure evidence

Every failed attempt writes a structured forensic bundle containing:

- Local Playwright form/Angular state
- Visible controls, stable selector hints and current committed values
- Active element, open DDS popups, overlays and invalid form controls
- Playwright MCP accessibility snapshot
- Playwright MCP network evidence
- Playwright MCP console evidence
- Chrome DevTools MCP DOM snapshot
- Chrome DevTools MCP network evidence
- Chrome DevTools MCP console evidence
- Recent local action events
- DOM state transitions
- Network and console history
- Persistent browser phase history
- Current screenshot
- Golden screenshot references
- Golden-image vision diagnosis
- Deterministic verification and judge diagnosis
- Chosen recovery action and Dell AIA advisor result

Primary artifacts are stored under:

```text
runtime_self_heal/<phase>/attempt_<N>_<classification>/
```

## Judge-gated deterministic trajectories

After a phase passes, the runtime writes:

```text
<phase>/validated_deterministic_trajectory.json
```

The trajectory stores no customer values. It stores:

- Ordered semantic field actions
- Angular `formcontrolname` and `name` selectors
- Accessible role/name fallbacks
- Label occurrence and row identity
- Last-known selector as non-primary evidence
- Executor type and required DOM events
- Conditional-child and overlay wait profile
- Angular rerender/rebind requirements
- Page fingerprint and route template
- Golden reference names
- Evidence channels used to approve the path

Only trajectories with passing deterministic, text and vision judges are promoted into Flow Pattern Memory and Portal Brain.

## Future-run behavior

For a structurally matching phase, the agent first uses the validated deterministic trajectory. It explores again only when:

- The live surface fingerprint changed
- A control is missing or ambiguous
- A committed value does not remain sticky
- A repeatable row does not produce the expected state transition
- A judge or golden comparison reports a real mismatch

All values are always loaded from the current input file. Memory never stores customer-entered values, credentials, tokens or upload contents.

## Safety

Unlimited retry does not override safety. The runtime can stop for:

- A proven unsafe or final-mutation request
- A closed browser or deliberate Ctrl+C interruption
- Intentionally unavailable SSO, HIP portal, MCP services, network or Dell AIA
- A persistent state where the only possible next action is prohibited

Save, Create, Submit, Delete, Deploy, Publish, Update, Remove, Enable, Disable and Confirm remain prohibited.
