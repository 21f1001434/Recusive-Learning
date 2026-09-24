# Course AgentQ Web-Agent Architecture + HIP Intelligence MCP Fix — 2026-07-20

## Objective

Integrate the useful concepts from the supplied Web Agent course code and architecture diagram into the existing Dell HIP Portal agent, without using MultiOn and without replacing the persistent single-Chrome/single-SSO runtime.

The implementation is designed to improve the recurring failure mode where the agent captures rich DOM/HTML/JS/CSS evidence but still chooses the wrong action, loses the active form, repeats a phase, or fails to learn from a prior successful or failed transition.

## Architecture implemented

```text
input.json / user goal
        ↓
Dell AIA GPT-OSS-120B task understanding
        ↓
Hierarchical phase and field task plan
        ↓
Value-free Web Representation Model
  DOM + accessibility + Angular/DDS + events + MCP evidence
        ↓
Action Model
  live binding + safety gates + bounded UCB ranking + memory priors
        ↓
Actor
  Playwright MCP / exact Python Playwright DDS transaction
        ↓
Transaction Critic
  exact value + stable rerender + explicit events + mutation protection
        ↓
GPT-OSS-120B / Gemma independent section judges
        ↓
Value-free success/failure trajectory memory and flow-pattern memory
```

## Why DOM capture alone was insufficient

Raw HTML and DOM events describe the page, but do not by themselves provide:

- a stable identity for rerendered Angular/DDS controls;
- a proof that the current surface is the intended Create/Wizard form;
- a ranked safe next action;
- a causal check that the action changed only the intended field;
- learning from a prior success or failure;
- drift detection between a remembered flow and the current live portal.

The new representation/planner/critic loop adds those missing layers while preserving the strict all-phase interaction policy.

## New modules

### `hip_id_agent/web_representation.py`

Creates `hip.web-representation.v1` from structural signals only. It emits:

- a 256-dimensional signed-hash embedding;
- structural and state fingerprints;
- normalized URL path;
- semantic controls and component state;
- surface-gate status;
- DOM transition summary;
- value-free console and network signatures.

Raw input values are excluded.

### `hip_id_agent/action_model.py`

Implements the HIP-safe Action Model and critic. Candidate actions are limited to:

- execute the uniquely bound action;
- verify an existing exact value;
- wait/rebind after rerender;
- reveal a structural parent;
- recommit a parent using real browser events;
- recover a stale overlay;
- stop on lost surface or ambiguous binding.

Save/Create/Submit/Delete/Deploy/Publish/Update/Confirm are not candidate actions.

### `hip_id_agent/trajectory_memory.py`

Stores value-free successful and failed transitions under:

```text
data/hip_memory/portal_brain/agentq/action_trajectories/
  trajectories.jsonl
  preference_pairs.jsonl
  manifest.json
```

The memory retains state fingerprints, semantic action identity, outcome, reason code and reward. It does not store customer values, credentials, tokens, authorization data or upload contents.

### `hip_id_agent/agentq_runtime.py`

Coordinates the representation, hierarchical plan, action model, critic, trajectory retrieval and persistence for every field in every stateful phase.

### `hip_id_agent/hip_intelligence_mcp_server.py`

Adds a local stdio MCP server with these tools:

- `build_web_representation`
- `plan_form_action`
- `critique_action_result`
- `retrieve_similar_trajectories`
- `record_trajectory_outcome`
- `detect_web_representation_drift`
- `get_course_architecture_manifest`

The MCP server opens no browser. It works alongside Playwright MCP and Chrome DevTools MCP.

## Course concepts used

- Web representation/embedding model
- Hierarchical planner
- Actor and critic separation
- AgentQ/MCTS-inspired bounded candidate ranking
- Long-term success/failure trajectory memory
- Reusable validated skills
- Value-free winning/losing preference pairs for optional offline tuning

## Course concepts intentionally not copied directly

### MultiOn

MultiOn is not used. The HIP agent already has a persistent authenticated Chrome tab controlled through Playwright MCP and observed through Chrome DevTools MCP. A MultiOn browser controller would create session ownership and SSO conflicts.

### Unbounded browser MCTS

The implementation uses bounded UCB-style ranking for one field at a time. It does not launch speculative destructive browser branches.

### Online DPO training

The runtime generates value-free preference pairs only. It does not retrain GPT-OSS-120B during the portal run.

## Runtime integration

The controller is started once in `dummy_fill_e2e.py`, attached to the shared BrowserSession, and reused for all phases:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

Before each field the runtime:

1. proves the active form surface;
2. builds the live web representation;
3. retrieves similar successful and failed trajectories;
4. builds the deterministic binding;
5. applies only a modest validated identity prior;
6. ranks safe actions;
7. executes through the existing strict transaction engine.

After each field it:

1. captures the new representation;
2. runs the deterministic critic;
3. stores the success or failure trajectory;
4. writes a value-free preference pair when a winning and losing action are available.

## Existing protections retained

- Structure-first form learning
- Hidden-parent and conditional-child rules
- Real click/check events for radio/dropdown/checkbox controls
- Bounding-box and hit-test stability
- Exact multi-select set proof
- Repeatable-row semantic identity
- Stale DDS overlay recovery without refreshing unsaved forms
- One-to-one control binding
- Previously committed field mutation protection
- Completed-phase evidence lock and no-repeat guard
- Deterministic/Text/Vision judge reconciliation
- Flow-pattern memory across same form families

## Configuration

Normal configuration enables HIP Intelligence MCP but permits fail-open startup for local development:

```yaml
mcp:
  use_hip_intelligence_mcp: true
  hip_intelligence_mcp_required: false
```

The strict Windows MCP profile requires it:

```yaml
mcp:
  use_hip_intelligence_mcp: true
  hip_intelligence_mcp_required: true
```

An empty command uses the same Python executable as the running agent.

## Per-run evidence

```text
agentq_course_architecture_manifest.json
hip_intelligence_mcp_startup.json
<phase>/agentq_runtime/hierarchical_phase_plan.json
<phase>/agentq_runtime/phase_initial_web_representation.json
<phase>/agentq_runtime/action_model_plan_*.json
<phase>/agentq_runtime/action_critic_*.json
<phase>/agentq_runtime/phase_agentq_summary.json
```

## Validation

- Python compilation: passed
- Course-architecture focused tests: 7 passed
- Complete project suite: 410/410 passed
- HIP Intelligence MCP startup from an external working directory: passed
- Web representation customer-value redaction: passed
- MultiOn dependency: absent
- Strict Windows profile requires all three MCP servers: passed
- Authenticated Dell HIP live rerun: not performed in this environment

The tests prove integration and regression safety; the next authenticated run is still required to validate current Dell portal behavior and produce live field trajectories.
