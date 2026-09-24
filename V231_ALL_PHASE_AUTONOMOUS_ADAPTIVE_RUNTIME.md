# V231 — Autonomous, Adaptive, Intelligent Form Runtime for All HIP Phases

## Objective

V231 applies the autonomous goal-driven browser runtime to the complete seven-phase HIP configuration mission:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

The mission input is the desired business state. It is **not** a click script. The live HIP portal is the runtime source of truth for structure, current controls, labels, DDS/Angular wrappers, tabs, conditional children, and current browser geometry.

## Authoritative execution loop

Every phase uses the same completion model:

`Observe -> Understand -> Plan -> Bind -> Act -> Verify -> Re-observe -> Learn/Rebind -> Continue`

The executor ladder is:

`AutoWebGLM semantic plan -> current-control proof -> PyAutoGUI MCP -> Playwright MCP -> Python Playwright -> exact read-back/effect proof`

PyAutoGUI is preferred when the current target has trustworthy physical geometry. Failure or absence does not stall the phase: the same semantically proven live control is canonicalized and handed to Playwright MCP. Python Playwright remains the final governed compatibility fallback.

## What is learned vs. what is never learned

Durable learning may contain business semantics and judge-verified structural relationships, such as:

- a parent selection reveals a particular child concept;
- a Rule Condition row contains Type, Attribute, Operator and Value concepts;
- a Transport Profile interface exposes interface-specific fields;
- a BizFlow routing action requires a Target after Action Type is selected;
- a live label is a verified semantic alias for an input field.

The agent does **not** persist these as durable knowledge:

- generated Angular/DDS element ids;
- CSS selectors tied to a DOM generation;
- nth-child/nth-of-type positions;
- viewport or screen coordinates;
- screenshot pixel positions;
- customer values inferred from another run.

Those are current-action evidence only.

## Phase-specific behavior

### Data Map

The V230 goal runtime remains intact. `+ Add` is discovered on the current Data Maps listing, the same-route Create Map drawer is proven, current controls are captured, input values are bound semantically, dependency-created controls are rediscovered, file controls are uploaded only when visible, and the entire target state is reverified before progression.

### Source and Target Document Type

Document Type retains its richer one-to-one transaction engine because repeated Attributes/Document Identifiers and parent-created children require strict row identity. V231 runs that specialized executor **inside the shared autonomous cycle**. Every cycle re-captures the live controls, may use Dell AIA/AutoGen advisory binding on ambiguity, executes the current target state, and verifies transaction/event evidence. File upload remains owned by the autonomous runtime so schema controls can appear after dependencies without being treated as unsupported state-graph actions.

### Rule

Existing Rule-specific helpers remain useful for creating the exact number of Conditions and interpreting Rule-specific semantics. They are no longer final completion authority. The shared autonomous runtime re-observes the Rule form and must prove the complete Rule target state. After safe branch exploration, autonomous reconciliation restores and proves the requested branch again.

### Source and Target Transport Profile

Interface-specific semantic helpers remain as accelerators because SFTP/FTP/HTTPS/AS2 forms can expose different conditional fields. The autonomous runtime owns final completion for both Source and Target TP. It re-discovers current fields after parent changes, repairs stale bindings, uses PyAutoGUI/Playwright through the common broker, and re-proves the target state after exploration.

### BizFlow

BizFlow remains structurally special: listing `+ Add` -> in-page template/card -> multi-tab form. V231 runs autonomous goal reconciliation **per visible BizFlow graph section/tab**. The current tab is the planning/binding scope, preventing fields on other tabs from being treated as missing. Repeatable process/routing rows are created by structural helpers, then each tab must pass autonomous exact-state proof before section judging/progression.

## Missing/new required fields

If Dell adds a new required control:

1. it is discovered from the current live form;
2. the agent attempts semantic mapping only to values that actually exist in the current mission input;
3. Dell AIA/AutoGen may advise a binding only to a real current live control;
4. if no trustworthy mission value exists, the phase returns `needs_input` / `NEEDS_INPUT` evidence;
5. the agent does not invent a default value merely to pass the section judge.

## Completion contract

A writable phase cannot pass because the expected text happens to be visible somewhere on the page. Completion requires:

- correct active form/surface;
- current controls discovered;
- mission values bound to current controls;
- authoritative interaction transaction(s) for writable controls;
- exact post-action read-back or selected-state proof;
- dependency/row identity consistency;
- no uncovered required live control lacking a real mission value;
- section judge approval where enabled;
- only then phase handoff.

Diagnostic continuation from a blocked phase is never represented as a verified handoff.

## Configuration

Default V231 configuration enables the shared runtime for all phase families:

```yaml
autonomous_form:
  enabled: true
  apply_to_data_map: true
  apply_to_document_types: true
  apply_to_rules: true
  apply_to_transport_profiles: true
  apply_to_biz_flow: true
  apply_to_all_form_phases: true
  max_adaptive_cycles: 5
  no_progress_cycle_limit: 2
  use_autowebglm_live_observation: true
  use_dell_aia_binding_advisor_on_ambiguity: true
  rediscover_controls_before_every_action: true
  require_exact_readback: true
  require_authoritative_executor_proof: true
  persist_only_judge_verified_semantics: true
  never_persist_selectors_or_coordinates: true
```

`--autonomous-mission` explicitly forces all of these phase-family switches on so an old local configuration cannot silently leave some phases deterministic-only.

## Runtime observability

`mission_trace.json` now records:

- `autonomous_all_form_phases: true`
- the autonomous goal loop description
- AutoWebGLM planner state
- primary/fallback executor information
- semantic gate state
- observed controls
- actual fills/clicks
- verification/judge state
- blockers and diagnostic continuation separately

The JavaScript control center banner shows `autonomous/adaptive ALL PHASES` when that runtime contract is active.

## Safety and mutation governance

Autonomy applies to understanding, navigation, form preparation, field interaction, dependency resolution, recovery and verification. It does not remove final mutation governance. Save/Create/Submit/Finish/Deploy/Delete-style actions remain subject to the existing mutation policy, live witness policy, and no-duplicate-dispatch safeguards.

## Main implementation files

- `hip_id_agent/autonomous_form_runtime.py`
- `hip_id_agent/stateful_form_runtime.py`
- `hip_id_agent/browser_session.py`
- `hip_id_agent/phase_form_entry.py`
- `hip_id_agent/autowebglm_bridge.py`
- `hip_id_agent/dds_control_driver.py`
- `hip_id_agent/datamap_kb.py`
- `hip_id_agent/doctype_kb.py`
- `hip_id_agent/rules_kb.py`
- `hip_id_agent/transport_profile_kb.py`
- `hip_id_agent/bizflow_kb.py`
- `hip_id_agent/mission_trace.py`
- `hip_id_agent/dummy_fill_e2e.py`

## Validation scope

The final V231 package is validated with the repository regression suite, Python compilation, YAML configuration parsing, wheel build, and an extracted-package smoke run. These tests validate the automation architecture and local/browser mocks. They are not a substitute for Dell production UAT; the final proof remains a live authenticated HIP mission on the user's Windows workstation.
