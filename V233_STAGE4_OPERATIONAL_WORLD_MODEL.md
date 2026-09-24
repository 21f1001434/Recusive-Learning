# V233 Stage 4 — Operational World Model and Autonomous Transition Planner

## Objective

Stage 4 makes the Stage-3 semantic website world model operational. The agent no longer uses memory only as a ranking/recovery hint: it can propose the next **semantic website transition** required to reach a mission goal, while the current live HIP page remains authoritative.

Core principle:

> **Memory proposes; live evidence authorizes.**

No remembered selector, coordinate, old DOM id, old option, or historical success can authorize an action by itself. Every action must be rediscovered on the current foreground surface, proved live, executed through the governed hybrid executor, and verified by post-action state/effect evidence.

## End-to-end planning loop

```text
Mission / input.json
        ↓
Stage 1 — live website understanding
DOM + accessibility + foreground surface + events + overlays + options
        ↓
Stage 2 — Agent Live View
what the agent sees / candidates / selected target / executor / verification
        ↓
Stage 3 — semantic world model
validated/candidate/negative controls, transitions, dependencies, portal-owned options
        ↓
Stage 4 — Autonomous Transition Planner
current state → desired state → safest live semantic transition
        ↓
Current target is re-proved
        ↓
PyAutoGUI MCP → Playwright MCP → Python Playwright fallback
        ↓
Exact effect / state verification
        ↓
Verified success → learn/promote transition
Failure/no-effect → negative evidence / self-heal
        ↓
Re-observe and continue until mission goal is proven
```

## Supported operational goal actions

The planner can reason about the following semantic actions:

- `open_add_form`
- `create`
- `edit`
- `save`
- `validate`
- `clone`
- `migrate`
- `deploy`
- `add_row`
- `next`
- `back`
- `expand`
- `more_actions`

These are semantic intents, not stored CSS selectors or recorded coordinates.

## Dynamic dropdown / Process Step intelligence

Dynamic dropdowns are resolved from the **currently mounted option list**.

Priority:

1. explicit mission/input value;
2. unique current option mentioned by the structured mission context;
3. one previously **validated** portal-owned choice that still exists in the current live option list;
4. otherwise `NEEDS_INPUT` / ambiguous — never guess.

Example:

```text
Mission value: SFTP_HAFT
Current live options:
  FTP
  SFTP HAFT
  HTTPS AS2

Result:
  SFTP HAFT
```

The mapping is semantic/canonicalized for the current run. The option must still exist live.

For BizFlow Process Step:

```text
Current options:
  Translation
  Passthrough
  Split

Mission says Translation → choose Translation.
Mission omits value but only one option is uniquely implied by context → choose it.
Memory remembers Passthrough but current mission says Translation → mission wins.
Memory remembers an option that is no longer live → memory is ignored.
Two options remain plausible → NEEDS_INPUT.
```

Successful portal-owned choices can be promoted into Stage-3 semantic memory only after effect verification.

## Create / Edit / Save / Validate / Clone / Migrate / Deploy

Stage 4 adds selector-free semantic fallback paths for operational website actions. The runtime can use a current live action even if no older capability id/selector exists, provided the semantic action is proved on the current page.

Examples:

```text
DETAIL → Edit → EDIT DRAWER → Save → UPDATED
DETAIL → Validate → VALIDATION RESULT
DETAIL → More Actions → Clone → CLONE DRAWER
DETAIL → More Actions → Migrate → MIGRATION FLOW
FLOW DETAIL → Deploy → DEPLOY RESULT / HISTORY
```

Overflow/compound actions remain scoped to the active entity/surface.

## Certified future-task integration

The Certified Future Task planner/executor explicitly preserves Stage-4 `semantic_action` steps. It does not force a live semantic action back through an unresolved legacy capability id.

A certified semantic action is still governed by:

- target/entity scope;
- current live semantic affordance proof;
- foreground ownership;
- mutation authorization policy;
- mutation dispatch evidence;
- post-action reconciliation.

### No duplicate mutation replay

For Create/Save/Migrate/Deploy and other mutations:

```text
physical dispatch attempted
        ↓
outcome becomes uncertain / tool times out
        ↓
DO NOT replay through another physical executor
        ↓
reconcile network/UI/dispatch evidence
        ↓
committed / not dispatched / uncertain
```

Stage 4 autonomy therefore does not weaken the existing no-duplicate-mutation guarantees.

## World-model relationship

Validated memory can influence the transition planner and target ranking only as a bounded prior. Live foreground/DOM/accessibility evidence always wins.

The system may remember:

- a semantic control worked in a particular phase/section;
- a transition successfully reached a new semantic state;
- a portal-owned dropdown choice caused specific children to appear;
- repeated failure/no-effect patterns.

It does not persist:

- CSS/XPath;
- generated DDS/Angular ids;
- DOM indexes;
- screen/viewport coordinates;
- bounding boxes;
- arbitrary customer-entered textbox values.

## Role-aware SFTP-HAFT deployment groups

The Stage-3 correction remains authoritative in Stage 4:

- Sender / Dell / Source → `da-sender-sftphaft-dce-shared`
- Partner / Receiver / Target → `pt-receiver-sftphaft-dce-shared`

This policy is SFTP-HAFT-specific. Explicit non-legacy overrides remain authoritative and other interface families do not inherit these defaults automatically.

## Primary Stage-4 implementation files

- `hip_id_agent/autonomous_transition_runtime.py`
- `hip_id_agent/dds_control_driver.py`
- `hip_id_agent/autonomous_form_runtime.py`
- `hip_id_agent/future_task_agent.py`
- `hip_id_agent/certified_future_task_agent.py`
- `hip_id_agent/website_world_model.py`
- `hip_id_agent/semantic_affordance.py`
- `hip_id_agent/browser_session.py`
- `hip_id_agent/config.py`
- `tests/test_v233_stage4_operational_world_model.py`

## Boundary of Stage 4

Stage 4 makes the learned world model operational for semantic workflow progression and dynamic option selection. It still does not claim production success against the Dell tenant from the build container. The current authenticated Dell HIP run remains the real UAT and the Agent Live View should be used to inspect any remaining portal-specific drift.
