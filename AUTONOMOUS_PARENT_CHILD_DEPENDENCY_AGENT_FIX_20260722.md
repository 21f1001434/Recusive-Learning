# Autonomous Parent–Child HIP Form Agent

## Objective

The mission is no longer treated as a collection of independent field fills. The agent now treats the HIP Portal as two connected dependency graphs:

1. **Mission dependency graph** — upstream objects and phases must be completed and independently judged before downstream phases begin.
2. **Form dependency graph** — every field, conditional child, repeated row and section is executed only after its structural and value-specific parents are committed.

The current values always come from the supplied `input.json`. Learned memory contains form structure, selectors, event sequences, waits and dependency topology, but not customer values.

## Mission sequence and entity dependencies

The autonomous mission covers:

```text
Data Map
Source Document Type
Target Document Type
Rule
Source Transport Profile
Target Transport Profile
BizFlow
```

The mission controller also enforces these semantic dependencies:

```text
Rule                 <- Data Map + Source Document Type
Source TP            <- Source Document Type
Target TP            <- Target Document Type
BizFlow              <- Source/Target Document Types + Rule + Source/Target TPs
```

A downstream phase cannot start merely because it is next in the loop. Its upstream phases must have exact-state evidence and passing judge results.

## Parent–child scheduler

Every phase state graph is compiled into `hip.autonomous-parent-child-contract.v1` before execution.

The contract contains:

- A topologically sorted action order
- Parent IDs for every child
- Section validity barriers
- Value-specific parent/child edges
- Repeatable-row sequence barriers
- Per-node mount, loading, stability and rebind waits
- Exploration or exploitation mode
- Cycle detection and fail-closed diagnostics

### Parent transaction

A parent is considered complete only after:

1. The exact control is uniquely bound.
2. The expected current-input value is selected or entered.
3. Required click/input/change/blur events are proven.
4. The value is stable across repeated observations.
5. Blocking validation is absent.
6. Previously completed fields remain unchanged.
7. Parent-triggered loading and animation settle.
8. Eligible children mount and become uniquely bindable.

Only then is the child released by the scheduler.

### Repeatable rows

Repeated rows are sequential transactions:

```text
Complete row 0 -> verify all row-0 fields -> create row 1 -> rebind row 1 -> fill row 1
```

The scheduler prohibits interleaving row values and prevents a later input row from reusing the first physical row.

## Exploration mode

Exploration is used when no judge-validated pattern matches the live form or when drift is detected.

Evidence includes:

- Local Playwright DOM and Angular state
- Playwright MCP accessibility, network and console evidence
- Chrome DevTools MCP DOM, network and console evidence
- HIP Intelligence MCP action planning and critique
- Active form/drawer identity
- DDS popup ownership and loading state
- DOM event and mutation windows
- Golden-image visual differences
- Parent/child scheduler state
- Earliest unresolved parent or child-mount gate

The agent explores only safe, unsaved structural interactions. Final mutation actions remain blocked.

## Exploitation mode

After deterministic verification plus text and vision judges pass, the successful path is stored as a value-free replay pattern:

- Ordered semantic nodes
- Parent/child dependency topology
- Stable `formcontrolname` and `name` selectors
- Accessible role/name fallbacks
- Row and section identities
- Correct executor and DOM event sequence
- Conditional-child wait and rebind profile
- Overlay settlement behavior
- Page and structure fingerprints

On the next structurally matching run, the agent exploits this validated path first. It cannot move a child ahead of a parent even when an older memory sequence suggests a different order. Any ambiguity or drift immediately returns the node to exploration mode.

## Timeout handling

A Playwright or MCP click timeout is not automatically a failed action. The runtime re-probes the owned control and accepts success only when the exact portal state is already committed. This prevents repeated clicks on dropdown options that Angular successfully selected before the transport call timed out.

## New evidence artifacts

At run root:

```text
mission_parent_child_contract.json
mission_state.json
mission_completion_report.json
```

Within each phase:

```text
parent_child_execution_contract.json
validated_deterministic_trajectory.json
flow_pattern_memory_match.json
flow_pattern_memory_promotion.json
mission_phase_readiness.json
```

Within every self-heal attempt:

```text
runtime_self_heal/<phase>/attempt_<N>_<classification>/dependency_scheduler_state.json
runtime_self_heal/<phase>/attempt_<N>_<classification>/forensic_evidence_manifest.json
```

## Safety boundary

The mission fills and verifies the forms. It does not click:

```text
Save, Create, Submit, Delete, Deploy, Publish, Update, Remove, Enable, Disable, Confirm
```

Unlimited retry applies only to safe recovery and unsaved form interactions. A dependency cycle or invalid mission dependency contract stops fail-closed rather than looping forever.

## Changed implementation

- Added `hip_id_agent/autonomous_dependency_runtime.py`.
- Integrated dependency scheduling into both shared state-graph executors.
- Added mission-level phase dependencies.
- Added parent/child forensic evidence to runtime self-heal and Dell AIA context.
- Extended validated deterministic trajectories and Flow Pattern Memory with value-free dependency contracts and node wait profiles.
- Added four mandatory interaction policies for DAG scheduling, sequential rows and state proof after transport timeout.
- Added `RUN_DEPENDENCY_AWARE_AUTONOMOUS_MISSION.ps1`.

## Verification

- Existing baseline: 465 tests
- New dependency-aware regressions: 11 tests
- Total source-tree suite: **476/476 passed**
