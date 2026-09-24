# HIP Portal Stateful Knowledge-Graph Runtime Fix — 2026-07-16

## Verdict

The MCP tools were not the main failure. Playwright MCP could act and Chrome DevTools MCP could observe. The orchestration layer still treated a HIP form as a flat collection of controls captured from an early/empty state.

The latest run `UHAUL-POASN-FULL-DUMMY-20260716-162218` proves this. Data Map passed. Source Document Type then stopped because parent selections revealed conditional children, but those children were not executable actions. The form showed the correct five Attribute rows and the correct `Derived From` parents, while Document Identifier `Value`, four Attribute `Expression/Value` controls, and DDS multi-select Usage values were not committed.

## Core architectural correction

The runtime now uses a phase-local state graph:

```text
current input.json object
        ↓
exact repeatable-row plan
        ↓
parent node commit
        ↓
live DOM/MCP state transition
        ↓
semantic child rediscovery
        ↓
child commit + exact verification
        ↓
section judge
        ↓
safe alternate-branch exploration
        ↓
target branch restoration + rejudge
        ↓
judge-approved Portal Brain / KG promotion
```

An empty form is discovery evidence only. It is never considered the complete executable schema.

## What was wrong in the latest run

1. The deterministic plan was generated from the initial 23-control form inventory.
2. Choosing `TRANSACTION_ROOT_ELEMENT` revealed Document Identifier `Value`.
3. Choosing `ELEMENT_IN_PAYLOAD` revealed Attribute `Expression/Value`.
4. These controls existed in the live form but were absent from the flat action list.
5. `Usage` is a DDS multi-select. The old code typed the complete comma-separated string, producing `No options found`.
6. The strict judge correctly blocked the run on five missing exact values.

## Implemented runtime behavior

### Target branch first

Every phase fills the branch required by the current input before exploring alternatives. This means dependent controls are learned in the state in which they are actually needed.

### Phase-local values only

The planner resolves values only from the matching object path, such as:

- `$.objects.source_document_type`
- `$.objects.rule`
- `$.objects.source_transport_profile`
- `$.objects.biz_flow`

Rule, Transport Profile or BizFlow values cannot leak into Document Type fields.

### Parent-child execution

For each state-graph node the runtime records:

- exact input path;
- section and row occurrence;
- parent dependencies;
- expected committed value;
- semantic locator hints;
- expected state transition;
- verification rule;
- executor and fallback executor.

After every parent commit, controls are recaptured. Dynamic DDS IDs are not reused as durable selectors.

### Repeatable rows

The current input array defines the exact row count. Row creation is verified by a delta of exactly one per safe `+ Add` click. Fields are resolved by section, row kind, row occurrence and label occurrence.

### Dropdowns, radios and multi-selects

- Single dropdown: choose one exact live option.
- Multi-select: choose each requested option independently and verify exact selected-set equality.
- Radio: choose the exact expected label/value.
- Text: input/change/blur followed by exact value verification.
- File: phase-specific accept/required contract followed by portal validation.

### Learning and fast replay

Each successful phase writes:

- state graph;
- target-branch execution report;
- observed dependency edges;
- semantic control registry;
- repeatable-row contract;
- exact deterministic plan;
- flash replay blueprint containing the graph and judge-bound execution.

The Portal Brain keeps candidate, validated and negative evidence separate. Target-branch knowledge is promoted only when deterministic DOM, text and required vision gates pass.

## Phase graph coverage for the supplied input

| Phase | Nodes | Dependency edges | Repeatable groups | Phase-local paths |
|---|---:|---:|---|---|
| biz_flow | 48 | 29 | {"flow_identifier": 2, "process_step": 2, "routing_condition": 2, "routing_action": 1} | Yes |
| data_map | 7 | 0 | {} | Yes |
| rule | 19 | 9 | {"condition": 2} | Yes |
| source_document_type | 29 | 11 | {"document_identifier": 1, "attribute": 5} | Yes |
| source_transport_profile | 15 | 10 | {} | Yes |
| target_document_type | 29 | 11 | {"document_identifier": 1, "attribute": 5} | Yes |
| target_transport_profile | 15 | 10 | {} | Yes |

## Major code changes

- Added shared stateful graph compiler/executor for Data Map, Document Types, Rule, both Transport Profiles and BizFlow.
- Added target-first Document Type execution for conditional Value/Expression controls.
- Added exact DDS multi-select selection.
- Connected all BizFlow tabs and the Configure Routing drawer to the shared state graph.
- Preserved phase-specific row/accordion logic, then used the graph as the final exact reconciliation gate.
- Added semantic section aliases for Basic/Source/Target/Routing tab labels.
- Added label occurrence and row occurrence resolution for repeated controls.
- Added generic target-branch knowledge documents for Portal Brain ingestion.
- Added state-graph execution to flash replay blueprints for every phase, not only Document Type.
- Kept the no-mutation barrier.

## Verification

- Python compilation: passed.
- Automated tests: 279 passed.
- Actual supplied input compiled into state graphs for all seven phases.
- All generated action input paths are phase-local.
- Regression tests cover conditional children, multi-selects, radios, repeatable groups, BizFlow tab aliases, generic row resolution and non-Document-Type replay execution.

## Honest live status

This environment cannot complete Dell SSO and execute an authenticated HIP run. Therefore the patch is ready for a controlled no-save rerun, but it is not claimed as a completed live pass. The rerun must prove exact values and section gates through BizFlow.
