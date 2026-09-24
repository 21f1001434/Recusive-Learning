# HIP Portal Exploration Agent — Implementation Verdict

## Verdict

Implemented as a first-class, safe form-knowledge component. The agent now learns more than a flat list of labels and options: it records value-specific parent-to-child state transitions and supplies those observed dependencies to the deterministic planner.

## Runtime architecture

1. Open the no-save Add/Create form.
2. Capture the live control registry and Playwright MCP accessibility state.
3. Identify structural parent controls such as System Type, Interface Type, Existing Account, Rule Type, Condition Type, Process Step Type and Action Type.
4. Enumerate the known values of each structural parent.
5. Select each safe parent value using official Playwright MCP as the primary executor.
6. Capture before/after live DOM state.
7. Record children that appeared, disappeared, became enabled/disabled, or changed options.
8. Restore the input.json-selected value after every alternate branch.
9. Persist the observed dependency graph.
10. Build future deterministic plans from the observed graph plus current input.json.
11. Use gpt-oss-120b only as a non-authoritative dependency classifier/recovery adviser.
12. Use the section judge to block progression until the selected branch is filled and verified.

## New implementation

- `hip_id_agent/portal_form_exploration.py`
- `FormExplorationPolicy`
- `run_portal_form_exploration(...)`
- `merge_section_knowledge(...)`
- Parent value branch exploration
- Input JSON to portal-field edges
- Repeatable `+ Add` to child-row knowledge
- Live state-delta dependency edges
- Playwright MCP primary selection path
- Explicit DDS Python Playwright fallback evidence
- Optional Dell AIA `gpt-oss-120b` dependency analysis
- Planner topological ordering from live-observed edges

## Form knowledge outputs

Every run writes under:

```text
<run>/portal_form_knowledge/
```

Including phase or section files such as:

```text
data_map_create_data_map_form_knowledge.json
source_document_type_create_document_type_form_knowledge.json
rule_create_rule_form_knowledge.json
source_transport_profile_create_transport_profile_form_knowledge.json
biz_flow_basic_details_form_knowledge.json
biz_flow_source_details_form_knowledge.json
biz_flow_target_details_form_knowledge.json
biz_flow_configure_routing_add_form_knowledge.json
biz_flow_form_knowledge.json
```

## Knowledge represented

- forms, sections and controls;
- stable semantic keys and selectors;
- input.json paths and expected values;
- all catalogued parent values;
- live-explored parent values;
- branch-specific child controls;
- controls enabled/disabled by a parent value;
- child dropdown option changes;
- repeatable row requirements;
- row-count effect validation;
- unexplored branches and restoration warnings;
- plan-ready dependency edges.

## Deterministic plan changes

`form_knowledge_plan.py` now loads the newest prior exploration graph and:

- adds observed parent-value preconditions to child actions;
- adds observed child effects to parent actions;
- topologically places parent selections before dependent child fills;
- exposes knowledge gaps in the plan;
- does not trust LLM-inferred edges as deterministic truth.

## Safety

The exploration agent never authorizes final Save, Create, Submit, Delete, Deploy, Publish or Update operations. Parent branches are explored only when a safe selector and a restore value are available. Every fallback and restoration failure is recorded.

## Validation

```text
python -m py_compile hip_id_agent/*.py: passed
pytest -q: 216 passed in 8.02s
```

A live authenticated Dell HIP Portal run was not possible in this environment. The next Dell-environment run must confirm the exact DDS behavior for every alternate parent branch.
