# Universal HIP Form Policy and Same-Flow Pattern Memory Fix

Date: 18 July 2026

## Objective

Apply the mandatory interaction rules to every HIP Portal form, not only the seven current full-dummy-fill phases, and add long-term memory that recognizes and safely reuses a previously validated flow pattern when the current flow has the same structural type.

## Universal HIP form coverage

The shared policy catalog now covers:

- Account
- Partner
- System
- Domain
- Deployment Group
- Data Map
- Document Type
- Rule
- Transport Profile
- BizFlow
- Workflow
- Transport Profile Orchestration
- Flow Orchestration
- Any new page under `/hybrid-integrations` through a generic HIP form fallback

Every catalog family inherits structural-parent discovery, explicit widget events, conditional-child visibility, animation and bounding-box stability, active-surface scoping, hit-testing, stale-node rebinding, exact-state verification, validation gates, repeatable-row rules, exact multi-select set proof, committed-field protection and final-mutation blocking.

## Universal action preflight

Generic BrowserSession click/fill operations now run a policy preflight before acting:

1. Resolve the target locator.
2. Check visibility.
3. When hidden, locate and explicitly open a safe structural parent such as a tab, accordion or details summary.
4. Wait for the target to become visible.
5. Require a stable bounding box and no active animation.
6. Require center-point hit-testing to pass.
7. Classify the current HIP form family.
8. Execute the real browser action.
9. Record a value-free candidate action observation.

This adds the policy to Partner/System/Account/Domain exploration paths and future HIP modules that do not yet have a dedicated state graph.

## Same-flow pattern memory

A new persistent memory is stored under:

```text
<data/hip_memory>/<portal_brain>/flow_patterns/
├── manifest.json
├── patterns.json
└── candidate_action_observations.jsonl
```

The memory stores only:

- Form family
- Structural fingerprint
- Semantic field/action signatures
- Parent-child dependency topology
- Repeatable-row shape
- Stable non-sensitive flow traits such as interface type or transaction type
- Successful semantic binding identities
- Successful action order
- Required wait/event policy

It never stores or reuses:

- Customer names
- Partner/System/Account IDs
- Credentials or tokens
- Free-text business values
- Uploaded file contents
- Current input values

## Pattern matching

A current graph is compared only with judge-validated patterns from the same form family. Similarity includes:

- Exact structure fingerprint
- Semantic node overlap
- Dependency overlap
- Action-sequence overlap
- Repeatable-row shape
- Stable enumerated flow traits

Default minimum similarity: `0.78`.

Examples:

- Source Transport Profile can teach Target Transport Profile when both have the same interface/row structure.
- A previously validated SFTP-HAFT TP flow can accelerate another SFTP-HAFT TP with different names and credentials.
- A flow with different interface structure, ambiguous live controls or portal drift returns to learning mode.

## Judge-gated promotion

A pattern becomes reusable only when:

- Stateful execution passes.
- The final form model is one-to-one.
- No ambiguous binding is present.
- No unintended mutation occurred.
- The independent section judge passes.

Unjudged successful patterns remain candidates. Failed patterns remain negative evidence. Neither can enable fast replay.

## Runtime behavior

Before each phase, the runtime writes:

```text
<phase>/flow_pattern_memory_match.json
```

After a judged phase pass, it writes:

```text
<phase>/flow_pattern_memory_promotion.json
```

A validated safe match adds replay hints to the state graph, but the current live DOM must still pass unique binding, visibility, event, stability, exact-value, validation and committed-field checks.

## New configuration

```yaml
brain:
  flow_pattern_memory_enabled: true
  flow_pattern_min_similarity: 0.78
  flow_pattern_max_patterns: 500
  flow_pattern_allow_cross_phase_same_family: true
```

No new CLI flag is required.

## Validation

- Python compilation: passed
- New focused tests: 9 passed
- Complete suite: 397/397 passed
- Live authenticated Dell HIP run: not performed in this environment
