# V233 Stage 3 — Persistent Semantic Website World Model

## Objective

Stage 3 converts verified observations from Stage 1 (website understanding) and Stage 2 (Agent Live View) into durable semantic knowledge that can improve later HIP runs without becoming a brittle replay/macro system.

The model is a **website world model**, not a store of customer payload values. It remembers how the HIP portal behaves: semantic controls, foreground states, verified transitions, dependencies revealed/hidden by actions, and portal-owned option branches.

## Trust model

Every remembered item has one of three trust levels:

- `candidate` — observed/verified but not yet strong enough to be treated as a high-confidence prior.
- `validated` — enough successful effect-verified evidence exists to guide ranking/planning.
- `negative` — repeated failures or contradictory/no-effect outcomes have reduced trust.

Knowledge never directly authorizes an action. The current live page must still prove the foreground surface, control role/name/section, enabled/visible state, ownership and option availability.

## What is learned

Examples of safe semantic memory:

- `Document Types → Add → Create Document Type drawer`.
- selecting a Document Identifier `Derived From` control reveals `Expression` / `Value` children.
- a Rule Conditions row contains a repeatable semantic dependency pattern.
- Transport Profile `Interface Type = SFTP HAFT` exposes an interface-specific branch.
- BizFlow `Configure Routing → Process Step` has portal-owned options such as Translation/Passthrough when those options were actually observed live.
- a verified action caused specific semantic controls to appear/disappear.

The world model stores semantic descriptors and hashes rather than DOM generations.

## What is never learned

Stage 3 explicitly strips/rejects:

- customer-entered textbox values and secrets;
- request payload values as website knowledge;
- CSS selectors and XPath;
- Angular/DDS generated ids;
- DOM indexes;
- PyAutoGUI screen coordinates;
- viewport coordinates and bounding boxes.

A dropdown/process-step choice may be remembered only if the exact selected label was present in the **live mounted portal option list** captured before the action. This makes values such as `Translation` portal metadata rather than customer data. Arbitrary text such as a partner name, identifier or customer expression is not persisted as world-model knowledge.

## Runtime integration

```text
Stage-1 live website observation
        ↓
semantic target candidates
        ↓
Stage-3 world-model hints (bounded prior only)
        ↓
live semantic ranking / AutoWebGLM context
        ↓
current target re-proof
        ↓
PyAutoGUI / Playwright action
        ↓
post-action exact + semantic effect verification
        ↓
PASS → promote semantic state/transition/dependency
FAIL → record negative evidence / decay confidence
        ↓
Stage-2 Agent Live View displays current memory status
```

Memory influence on target ranking is intentionally bounded. It cannot make a background or semantically incompatible control outrank a current foreground control merely because an older run succeeded there.

## Dependency learning

For every verified transition the runtime compares value-free semantic control sets before and after the action. It records `revealed_controls` and `hidden_controls`.

This enables learning of dependencies such as:

```text
Interface Type
  └─ SFTP HAFT
       ├─ Environment
       ├─ Existing Account
       ├─ Folder controls
       └─ Documents Supported
```

or:

```text
Process Step
  └─ Translation
       └─ current routing child controls
```

The exact live page is re-observed on every future run; memory only tells the planner what relationships have previously been verified.

## Planning and recovery

Validated semantic transitions are supplied to AutoWebGLM as concise, value-free hints. Runtime self-heal can also see prior successful semantic transitions for the current phase. Recovery remains constrained by the existing governed allow-list and mutation safeguards: memory cannot autonomously bypass Create/Submit/Deploy protections.

## Agent Live View

Stage 2 now also exposes Stage-3 memory status for the currently selected target:

- matching memory trust/confidence;
- validated/candidate/negative control counts;
- validated/negative transition counts;
- remembered portal-owned choices;
- explicit `live_reproof_required=true`.

This makes it visible when the agent is using previous verified experience versus reasoning from a new surface.

## SFTP-HAFT deployment-group contract

Stage 3 also corrects the canonical role-specific SFTP-HAFT defaults:

- Sender / Dell / Source: `da-sender-sftphaft-dce-shared`
- Partner / Receiver / Target: `pt-receiver-sftphaft-dce-shared`

The policy is **SFTP-HAFT-specific**. It migrates blank/legacy generic SFTP-HAFT values to the role-specific defaults. An explicit non-legacy deployment group remains authoritative. FTP, HTTPS-AS2 and other interfaces do not inherit these defaults unless their live/input contract explicitly says so.

The UHAUL full/sample inputs and state-graph/TP seed logic use the same policy.

## Files added/changed

Primary Stage-3 implementation:

- `hip_id_agent/website_world_model.py`
- `hip_id_agent/deployment_group_policy.py`
- `hip_id_agent/semantic_control.py`
- `hip_id_agent/browser_session.py`
- `hip_id_agent/runtime_self_heal.py`
- `hip_id_agent/agent_live_view.py`
- `hip_id_agent/transport_profile_kb.py`
- `hip_id_agent/stateful_form_runtime.py`
- `backend/app.py`
- `webui/index.html`
- `webui/app.js`
- `config.yaml`
- `config.example.yaml`
- `config.mcp-required.windows.yaml`
- `examples/uhaul_poasn_full_dummy_input.json`
- `examples/uhaul_datamap_dummy_input.json`
- `examples/uhaul_doctype_dummy_input.json`
- `knowledge_base/HIP_Unified_Deep_KB.json`
- `tests/test_v233_stage3_website_world_model.py`

## Boundary of Stage 3

Stage 3 makes memory available to current target ranking, AutoWebGLM context, observability and safe recovery. It does **not yet make learned world-model transitions the authoritative workflow planner for all Create/Edit/Save/Validate/Clone/Migrate/Deploy progression**. That is intentionally reserved for Stage 4 after the learned evidence is visible and audited.
