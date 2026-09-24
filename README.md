## V243R13 — All-phase completion + learning/RSI unblock (2026-09-24)

Fixes missions stalling at Data Map (`goal not proven ... failed_attempts: []`, *exact checkpoint: not proven*). It also fixes the hidden blockers waiting in every later phase and in learning.

- **All 7 phases and the task box:** secret masking no longer hides `authoritative_*` proof flags. They were turned into `"***MASKED***"`, so no strict phase gate, exact checkpoint, `Looks correct` promotion or task-box form fill could ever pass.
- **Recursive self-improvement now runs after every Start mission**, not just after task-box and production runs: replay dreaming, model-champion dreaming and skill review. Results are in the run's `recursive_self_improvement.json` and in the Control Center RSI panel.
- **Learning:** agent-owned keys (`session_id`, `task_tokens`, …) are no longer masked when saved.
  - Start teaching → Finish & learn now captures the demonstration.
  - Learned recipes, skills and replay policies match new tasks after reload.
- **"Already exists" is existing-object evidence**, not an error, on every create form, as shown in the golden screenshots:
  - Map identifier / Name / Rule Name / Transport Profile / Business Flow Name;
  - applies in both executors and in the validation gate;
  - a row-level duplicate or a conflicting object still blocks.
- **Portal-owned disabled fields** (Version, Rule Type, Rule Scope) verify against their displayed value. A mismatch reports `HIP_READONLY_PORTAL_VALUE_MISMATCH`.
- **Document Type Status switch** is now recognised and operated as a switch. Previously Source/Target Document Type could never be proven.
- **Failures name the field and the unmet check** for every phase.
- `websockets==15.0.1`, so `pip install -r requirements.txt` resolves with `browser-use==0.13.8`.

See `V243R13_DATAMAP_LEARNING_UNBLOCK_FIX_20260924.md`. Apply with `APPLY_V243R13_IN_PLACE.ps1`.

## V243R12 — Continuous Portal Learning + Dell On-Prem Model Orchestra

V243R12 makes **real filling, clicking and navigation the training experience** for the HIP operator. Exact-verified interaction sequences are promoted into value-free Capability Graph / Portal Brain / trajectory / replay knowledge so later complex search, edit, fill, Rules, Transport Profile and BizFlow tasks can exploit what earlier runs taught the agent.

The live AutoWebGLM browser-decision layer now uses the Dell On-Prem model portfolio during learning and complex tasks instead of silently falling back to the default `gpt-oss-120b` client. Planning, action selection, judging and recovery use role-aware champion/challenger tournaments across the configured Dell models that are actually reachable. Availability and usage ledgers make the runtime model participation inspectable. Once a skill is repeatedly proven, fast exploitation may collapse to the best downstream-proven champion; drift or a complex/new task reopens the portfolio.

R12 keeps current `input.json` as the value authority, current live browser evidence as the action authority, and mutation governance unchanged. Long-term learning stores semantic structure/effects only—never customer values, raw selectors, XPath or screen coordinates.

See `V243R12_CONTINUOUS_LEARNING_MODEL_ORCHESTRA_20260923.md`.

## V243R11 — Interactive Teaching + Immediate Deterministic Promotion

- Human `Looks correct` + exact reproof + judge PASS atomically promotes the phase to deterministic semantic replay.
- Added live interactive teaching: Start teaching → navigate/click in HIP → Finish & learn → exact reproof → supervised promotion.
- Accepted phases cannot be reopened by non-authoritative learning/API/observability enrichment.
- Demonstrations persist semantic routes/actions only; no customer values, raw selectors, or coordinates.

# HIP Portal Agent v2.4.3

## V243R6 — Judge Reconciliation + One-Time Learning HITL

V243R6 fixes model-judge false negatives after a phase has already been filled correctly. Exact browser readback/completion evidence is reconciled with text/vision judges; an on-prem multi-model judge panel is consulted on disagreement, and every newly learned phase can pause once for an operator **Looks correct / Needs correction** review whether the automated judge passed or blocked it. Human/model reconciliation can never bypass failed exact evidence. Human review also becomes downstream judge-model feedback and learning evidence. See `V243R6_JUDGE_RECONCILIATION_HITL_FINAL_20260922.md`.

## V243 R2 — Production Hardening Final

This package includes an additive V243 R2 hardening pass over the V243 production runtime: execution-input immutability re-checks, governance-ledger tamper blocking, long-run lease heartbeats, a correctly honored single-session switch, strict safe-review allowlisting with bundle hashes, and sanitized failure diagnostics. See `V243R2_HARDENING_ADDENDUM_20260920.md`.


## V243 — Production End-to-End Runtime Closure

V243 turns the existing portal-learning stack into a single production lifecycle. A production request now passes through **doctor/preflight → governance → single-browser lease → planning → live execution → exact verification → audit journal → safe review bundle**. The Control Center is served by the same FastAPI process, so Bun is optional for normal operation.

Production safety and operability additions include: cross-process single-browser locking with stale-lock recovery, request/config/input fingerprinting without storing business values, duplicate/unresolved mutation quarantine, role/approval/three-key mutation governance, before/after mutation evidence, SHA-256 hash-chained execution journaling, fail-open MLflow observability, rollback/compensating-action guidance, collision-resistant run ids, one-command production doctor/execution, and a redacted review ZIP that excludes screenshots/raw portal evidence by default.

### Recommended production flow

```powershell
# 1. Start backend + Control Center in one process
python -m backend

# 2. Non-mutating readiness check
python -m hip_id_agent.cli production-doctor `
  'Fill the U-HAUL configuration from input.json' `
  --config .\config.yaml `
  --input-json .\input.json

# 3. Full governed execution (read-only tasks need no mutation flags)
python -m hip_id_agent.cli run-production-e2e `
  'Fill the U-HAUL configuration from input.json' `
  --config .\config.yaml `
  --input-json .\input.json
```

For Save/Create/Edit/Deploy/Migrate/Delete tasks, the existing mutation gate is still mandatory. See `V243_PRODUCTION_END_TO_END_SOLUTION.md`.

## V242 — Downstream-Proven Model Promotion + Release Closure

V242 hardens V241 model routing so a proposal-only tournament winner can never become a durable champion before the real browser outcome is known. Durable role/task champions are promoted only from downstream task evidence, task-specific scores are maintained separately from generic role scores, and model candidates are filtered by their declared role capability in addition to modality. The recursive self-improvement engine now honors the replay/model/skill update switches, and the standalone wheel smoke test imports the MLflow, replay, skill, model-portfolio and recursive-improvement components from their actual packaged modules.


## V241 — Recursive Self-Improvement + On-Prem Multi-Model Champion/Challenger

V241 extends the replay/dreaming runtime with a bounded recursive self-improvement loop and an On-Prem-only Dell AIA model portfolio. Multiple candidate models may reason in parallel, but they are **proposal-only**: challenger models never click, type, save, validate, deploy, or mutate the portal. The governed browser executor remains the only action authority.

The allowed model catalog is restricted to the On-Prem models supplied in the Dell AIA reference screenshots: `gpt-oss-20b`, `gpt-oss-120b`, `mistral-small-3-1-24b-instruct-2503`, `llama-3-2-3b-instruct`, `llama-3-3-70b-instruct`, `florence-2-large-ft`, `pixtral-12b-2409`, `gemma-3-27b-it`, plus `nomic-embed-vision-v1-5` as an image-embedding specialist. Cloud/GCP/Anthropic models are rejected by the portfolio catalog.

Model selection is outcome-driven. Each tournament records contract quality and latency; the final downstream reward comes from the real portal result: goal success, exact `input.json` coverage, exact readback, repeatable-row correctness, verified mutation outcome, recovery cost, and overall replay-policy score. The router keeps champions per role (planning, action selection, judge, recovery) and can use challengers during exploration. Once a champion has enough successful evidence, exploitation collapses to a single fast model until drift or lower reward reopens the challenger pool.

Recursive self-improvement is deliberately bounded: verified runs update replay policy, model routing and skill confidence, then run a small number of offline dream cycles. The engine does **not** rewrite Python/JavaScript source code at runtime. Current `input.json`, current live portal evidence, and the mutation gate remain authoritative on every action.

## V240 — Exploration / Exploitation + Dreaming Replay Policy Improvement

V240 adds a persistent replay-policy engine above AgentQ, induced skills, Portal Brain and MLflow. Every live run becomes a value-free replay episode scored on whether the requested goal was actually reached, whether every applicable `input.json` value was exact-readback verified, whether repeatable rows were preserved, whether the requested mutation was verified, and how much recovery/exploration work was required.

Historical runs are cached on disk and become a replay world. Offline **dreaming** evaluates multiple candidate exploration/exploitation policies against that stored history and promotes the best replay-scoring policy. The next run starts in **exploration**, **hybrid**, or **exploitation** mode based on support, success rate, confidence and drift evidence. A validated induced skill or replay workflow can become the fast path, but every real browser action is still resolved and authorized against the current live page.

Policy memory never stores customer values, CSS selectors, XPath, screen coordinates, viewport coordinates or bounding boxes. Current `input.json` remains the value authority; current live portal evidence remains the action authority; the existing mutation gate remains mandatory.

The Control Center now exposes the replay-policy cache, recent replay episodes, policy version, success rate, confidence and explore/hybrid/exploit counts. MLflow receives policy-selection, replay-score and dreaming events asynchronously.

## V239 — Universal portal learning + Skill Induction Engine + MLflow Async

V239 turns the HIP browser agent into a governed portal-learning operator rather than a workflow limited to the original seven HIP phases. The agent can observe unfamiliar page families and actions, execute user-requested activities from current live evidence, fill forms from the current `input.json`, and promote exact-verified successful trajectories into reusable **induced skills**.

A validated skill stores only value-free workflow structure: action order, page-family context, semantic field identity, row/section relationships, and verification rules. It does **not** store customer values, environment-specific URLs, CSS selectors, XPath, browser ids, screen coordinates, viewport coordinates, or bounding boxes. On reuse, the skill is instantiated with the current task, current portal URL, current entity and current `input.json`; every action is re-proven on the live page before execution.

If a learned non-mutating step drifts, the skill is penalized and the agent falls back to adaptive live discovery. Mutation-step failures are never blindly retried. Repeated contradictions demote a skill to `drift_suspect`; old skills lose effective confidence through time-aware decay. Successful rediscovery can reinforce the skill again.

The Universal Portal Operator supports:

- user-requested navigation, search, create, edit, fill, save/update, validate, clone, migrate, deploy, delete and learned future actions;
- bounded adaptive execution of previously unknown visible actions explicitly named in the user request;
- exact runtime-input coverage for arbitrary forms, including dynamically revealed child controls and repeatable rows;
- induced-skill fast replay with live semantic re-proof and adaptive fallback;
- a Control Center skill library showing validated, stale and drift-suspect skills;
- asynchronous MLflow observability for mission, step, judge, recovery, input-coverage and skill events.

Governance remains strict: `input.json` is the value authority, the live page is the action authority, and mutation actions still require the existing explicit mutation gate. Skill memory is advisory only.

### Universal portal task

```powershell
python -m hip_id_agent.cli run-portal-task `
  'Open Partner Settings, edit "ACME", fill from input json, save, validate and deploy' `
  --input-json .\input.json `
  --input-root '$.objects.partner' `
  --allow-portal-mutation `
  --confirmation 'ALLOW HIP MUTATION'
```

The first exact-verified successful run can induce a reusable skill. A later compatible request automatically activates that skill when its match score and effective confidence pass the configured thresholds. Current values are never reused from the previous run.

## V238 — Asynchronous MLflow observability

V238 adds MLflow 3.16.1 tracking through the lightweight `mlflow-skinny` client. Mission, phase, judge, retry/block, input-coverage, duration and final-result telemetry is emitted asynchronously. MLflow is observability-only: it cannot authorize, block, fill, click, save, deploy, or alter the HIP mission result. When no remote tracking URI is configured, the runtime uses a local filesystem MLflow store under the HIP run root.

## V237 — Runtime input ledger + active golden-reference guidance

V237 makes the actual runtime `input.json` the authoritative fill contract. Every applicable nonblank input leaf is reconciled against the current live form on every adaptive cycle. Golden screenshots are structural/visual guidance only; `input.json` remains the sole value authority and the current HIP page remains the sole action authority.

V237 retains the V236 all-supplied-field completion contract, V235 browser-session ownership/disconnect recovery, and V234 multi-color visual intelligence. SFTP-HAFT role defaults remain `da-sender-sftphaft-dce-shared` for Sender/Dell/source and `pt-receiver-sftphaft-dce-shared` for Partner/Receiver/target unless live input/portal evidence supplies a different explicit group.

## V233 Stage 3 — Persistent semantic website world model

Stage 3 adds a persistent, value-free website world model above Portal Brain/flow-pattern memory. Only effect-verified live browser outcomes can promote semantic controls, states, dependencies, transitions, or portal-owned dropdown choices. Success raises confidence; failed/no-effect actions create negative evidence and reduce trust. Live DOM/accessibility/vision proof always remains authoritative.

The world model is used as a bounded prior for semantic candidate ranking, AutoWebGLM planning context, Agent Live View, and safe runtime self-heal recommendations. It never persists customer-entered values, CSS/XPath selectors, generated DDS/Angular ids, screen coordinates, viewport coordinates, or bounding boxes.

SFTP-HAFT deployment groups are role-aware: Sender/Dell/source defaults to `da-sender-sftphaft-dce-shared`; Partner/Receiver/target defaults to `pt-receiver-sftphaft-dce-shared`. This policy is scoped to SFTP-HAFT and preserves explicit non-legacy overrides.

See `V233_STAGE3_PERSISTENT_WEBSITE_WORLD_MODEL.md` and `BUILD_VERIFICATION_V233_STAGE3.md`.




## v2.3.2 Final hybrid fail-closed hardening

V232 keeps the V231 all-phase autonomous goal runtime and hardens the live executor/evidence contract. Strict live completion now fails closed unless the inner phase executor explicitly proves both exact execution and authoritative execution; missing proof fields cannot default to success. Adaptive replay blueprints are semantic only and strip selectors, XPath, screen/viewport coordinates and bounding boxes before persistence.

Normal `config.yaml` and the Control Center now use a true hybrid executor quorum: PyAutoGUI MCP, Playwright MCP and governed Python Playwright are attempted according to the action policy, while Chrome DevTools MCP and HIP Intelligence MCP remain valuable evidence/intelligence witnesses but are not mandatory unless strict `--require-mcp` or `config.mcp-required.windows.yaml` is selected. Playwright MCP is eligible for live form dispatch only after its current URL is proven to match the authenticated Python-Playwright HIP surface; a mismatched MCP is quarantined instead of being allowed to click a stale tab.

`run-section` uses the same adaptive `--allow-executor-fallback` contract as full missions. V232 repository certification collected and passed 1,115/1,115 tests. See `V232_FINAL_HYBRID_HARDENING.md` and `BUILD_VERIFICATION_V232.md`.


## v2.3.1 Autonomous adaptive execution for all seven form phases

V231 generalizes the V230 Data Map goal runtime to the complete HIP form mission: **Data Map -> Source Document Type -> Target Document Type -> Rule -> Source Transport Profile -> Target Transport Profile -> BizFlow**. The current live portal is authoritative on every cycle. AutoWebGLM observes/plans, HIP semantic binding proves a real current control, PyAutoGUI MCP is preferred for physical interaction, Playwright MCP is the immediate deterministic fallback, Python Playwright is the final compatibility fallback, and exact read-back plus authoritative transaction evidence is required before phase completion.

Phase-specific code is retained only where it represents business semantics or structure (for example repeatable Rule Conditions, Document Type parent/child fields, Transport Profile interface-dependent controls, or BizFlow tabs/routing rows). It is no longer the final completion authority. Each phase is re-observed and reconciled by `autonomous_form_runtime` until its input-goal state is proven, a real missing required input is reported as `NEEDS_INPUT`, or bounded no-progress/wall-clock guards fail closed. Selectors, Angular-generated ids, DOM indexes and screen coordinates are never durable learning.

The Mission Trace runtime contract now exposes `autonomous_all_form_phases=true`, and the web UI banner shows **autonomous/adaptive ALL PHASES**. See `V231_ALL_PHASE_AUTONOMOUS_ADAPTIVE_RUNTIME.md` and `BUILD_VERIFICATION_V231.md`.

## v2.3.0 Autonomous adaptive Data Map execution

V230 converts Data Map from a fixed field-order routine into a goal-driven live form agent. The screenshots/verified HIP knowledge are priors only. Every run re-observes the live same-page Create Map surface, binds current controls semantically, uses AutoWebGLM on every governed interaction, prefers PyAutoGUI MCP when its physical target is trustworthy, falls back immediately to Playwright MCP/Python Playwright, verifies exact committed values, learns successful semantic relationships, and retries with bounded anti-stagnation recovery until the goal is proven or fails closed. Transient selectors and screen coordinates are never persisted as portal knowledge.

## v2.2.9 Hybrid authoritative form execution

V229 fixes the live failure where a phase could show `0 filled` but still appear to hand off. AutoWebGLM remains the primary semantic planner, but every writable DDS control now executes through the same governed broker: **AutoWebGLM intent alignment -> semantic target proof -> PyAutoGUI MCP primary physical action -> Playwright MCP fallback -> Python Playwright compatibility fallback -> exact read-back**.

The broker now covers text fields, search fields, DDS single/multi-selects, switches, checkboxes, radios, tabs, repeatable-row Add controls and the phase-level structural `+ Add`. Before Playwright MCP executes a target, the proven Playwright Locator is canonicalized to a unique current-DOM CSS selector; human descriptions such as `Data Maps top-right + Add` are never passed as CSS.

Data Map supervised portal evidence is encoded only as value-free semantic signatures (`hip_id_agent/hip_surface_ground_truth.py`): same-route `Create Map` drawer, page-level `Add`, Map Identifier/Status/Map Name/Map Class/Contivo version/Map Data controls, and negative row actions. `Status` is correctly treated as a DDS switch/checkbox when that is what the live DOM exposes. No screenshot coordinate, Angular generated id, tenant id, or customer value is persisted as ground truth.

Strict phase completion requires exact writable-field verification **and** authoritative interaction provenance. A visible value without a successful broker transaction is not enough. A blocked phase may be traversed for diagnostics, but Mission Trace records `continued_from_blocked`; it can no longer say `handoff verified`.

Default `config.yaml` is hybrid: PyAutoGUI MCP is preferred but optional. If it is unavailable or cannot prove an effect, Playwright MCP can execute the same proven target. `config.mcp-required.windows.yaml` remains the explicit strict PyAutoGUI-MCP-required profile. See `V229_HYBRID_AUTOWEBGLM_FORM_EXECUTION.md` and `BUILD_VERIFICATION_V229.md`.

## v2.2.8 PyAutoGUI MCP primary interaction runtime

V228 changes the HIP browser action ladder to **semantic proof → PyAutoGUI MCP primary physical interaction → Playwright MCP fallback/verification → Python Playwright compatibility fallback**. Clicks, form/search typing and key presses use the desktop MCP first. All HIP section openers remain same-page/in-page. If a visible page-level `+ Add` cannot be resolved from DOM/accessibility, high-confidence Gemma/Browser-Use perception can locate that structural control and PyAutoGUI MCP can click its viewport coordinate; mutation controls are excluded from visual-coordinate recovery.

The repository also ships `RUN_BROWSER_USE_WEBUI_OPTIONAL.ps1`. The generic Browser-Use WebUI is an optional isolated debugging/own-browser sidecar; it is not allowed to compete with the HIP mission controller for mutation authority. See `V228_PYAUTOGUI_PRIMARY_BROWSER_USE_WEBUI.md`.

v2.2.7 fixes the live-readiness blocker shown after long-running Windows tests: an expired/mismatched Windows runtime certificate is now auto-renewed by Live GO/NO-GO when static/browser/model/path prerequisites are healthy. The renewal still performs the real non-mutating Chrome + Dell SSO + Playwright/DevTools/HIP Intelligence + Dell AIA + PyAutoGUI MCP certification. PyAutoGUI MCP is also promoted to an active governed recovery channel immediately after Playwright MCP fails for semantically proven safe/structural clicks, exact-verified business-field fills, and key actions; final tenant mutations remain Playwright-governed. v2.2.5 authoritative execution, v2.2.4 form-entry/BizFlow state machines, Chrome-first, UTF-8 safety, and uncapped Dell AIA text/vision remain included.

## v2.2.3 Chrome-first live blocker hardening

- Google Chrome Stable is the default persistent HIP browser (`portal.chromium_channel: "chrome"`). Edge is the first startup-only fallback and Playwright Chromium is the final startup fallback. Once Dell SSO succeeds and the mission owns a browser session, the runtime does not switch browsers underneath that mission.
- Windows subprocesses force `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, and `PYTHONLEGACYWINDOWSSTDIO=0`; stdout/stderr are reconfigured to UTF-8 with replacement-safe error handling so portal/model text such as non-breaking hyphens cannot crash the mission with `UnicodeEncodeError`.
- Data Map, Document Type, Rule, Transport Profile, and BizFlow section-judge expectations now come from `compile_phase_state_graph()` with exact semantic field, section, repeatable-row identity, expected value, and input path. The old generic `input_value[N]` flattening path is not used for these governed phases.
- Post-fill evidence includes every non-empty live form control, including ordinary enabled/writable/non-required controls, preventing successfully filled values from disappearing before deterministic judging.
- Destination navigation during a phase handoff is traced to the destination phase while cleanup remains attributed to the source phase. The destination phase also receives an explicit verified-route observation after handoff.
- Blocked phase cards expose concise deterministic missing-field and text/vision judge diagnostics instead of only a generic BLOCKED status.

## v2.2.2 uncapped Dell AIA output + response-quality hardening

- Text and vision requests are **native/uncapped by default**: no `max_tokens` or `max_completion_tokens` field is sent.
- Optional positive overrides remain available via `AIA_MAX_OUTPUT_TOKENS`, `AIA_TEXT_MAX_OUTPUT_TOKENS`, and `AIA_VISION_MAX_OUTPUT_TOKENS`. Blank/0/`unlimited` means no cap.
- `gpt-oss-120b` final assistant output is preferred over `reasoning_content`/analysis, preventing hidden/intermediate reasoning from corrupting strict JSON judges.
- Dell/OpenAI response extraction accepts content parts, choice-level output aliases, Responses-style output containers and reasoning-only fallback when no final content exists.
- Vision preflight, section vision judge, normal recovery vision and optional aggregate vision verification all use the same response extraction policy.
- Strict vision JSON calls reject malformed non-JSON output instead of silently treating it as a successful structured judge result.
- The JavaScript Control Center shows a masked model-response preview and whether output is `native/uncapped` or explicitly configured.
- Input/context character budgets remain bounded for safety; this change removes the **model output token cap**, not the agent's evidence-size safeguards.

## v2.2.0 Dell AIA + Gemma vision runtime reliability

The Windows runtime now loads `.env` deterministically, accepts `VISION_MODEL_NAME=gemma-3-27b-it`, validates vision with a real 64×32 image, tolerates Dell AIA response-envelope variants, and prevents large PyAutoGUI MCP screenshot messages from tripping asyncio's default stdio line limit. Set `HIP_ENV_FILE` when the desired `.env` lives outside the project root.

## v2.1.9 live Windows runtime certification

Before a real HIP mission, certify the actual workstation once (default certificate TTL: 60 minutes):

```powershell
python -m hip_id_agent.cli certify-live-runtime --config config.yaml
```

A headed Edge/Chrome window is used. Complete Dell SSO if prompted. The certification is non-mutating and proves the actual browser/CDP session, Playwright MCP, Chrome DevTools MCP, HIP Intelligence MCP, Dell AIA text/vision, and PyAutoGUI MCP read-only desktop channel. It writes `runs/.hip_runtime/live_runtime_certificate.json`. The shipped config requires that recent, untampered certificate before the existing Live GO/NO-GO can return GO.

Web UI equivalent: **Certify Windows runtime** → complete SSO if needed → **Run Live GO/NO-GO** → **Start mission**.

## v2.1.8 PyAutoGUI MCP tertiary desktop fallback

- Adds the community `pyautogui-mcp` server as an optional Windows-only MCP channel, pinned to `2026.1.101837`.
- Keeps the execution order strict: AutoWebGLM + HIP Intelligence semantic proof -> Playwright MCP for normal web controls -> deterministic Playwright compatibility fallback where already allowed -> PyAutoGUI MCP only as the last visible-desktop fallback.
- PyAutoGUI MCP publishes explicit capability evidence for screen size, cursor position, click, write, key press, hotkey and screenshot tools.
- The existing direct in-process PyAutoGUI helper is retained only as a compatibility fallback when the MCP server is unavailable and `allow_local_fallback_if_mcp_unavailable=true`.
- Desktop fallback still requires a stable Playwright bounding box for ordinary HIP controls, and exact trusted-click/value/effect verification remains authoritative after the action.
- Native/browser-chrome coordinate recovery is available only with explicit high-confidence evidence (default >=0.97) from `vision`, `native_dialog`, or `browser_chrome` provenance. It is not used for normal DOM controls.
- Final Save/Create/Submit/Delete/Deploy/Publish/Update-style mutations are blocked through PyAutoGUI MCP by default.
- Live GO/NO-GO reports PyAutoGUI MCP as a warning when unavailable because it is optional by default; setting `pyautogui.mcp_required=true` makes it a blocker.
- No Selenium MCP, Puppeteer MCP, TypeScript MCP, second generic web executor, or arbitrary Python-execution MCP tool was added.


## v2.1.7 HIP Semantic Control MCP / Website Understanding

- AutoWebGLM remains the primary goal/action planner; it cannot directly authorize a physical click from visual plausibility alone.
- Official Playwright MCP is the sole governed physical executor after Layer-11 proof. `browser_find` is used for accessibility-tree narrowing and ephemeral ref resolution; normal multi-text-field forms can be executed through one verified `browser_fill_form` call.
- Chrome DevTools MCP is an independent DOM/network/console witness, not a competing executor.
- HIP Intelligence MCP now exposes: `hip_get_current_surface`, `hip_get_form_schema`, `hip_find_control`, `hip_find_owned_popup`, `hip_get_repeatable_rows`, `hip_get_required_fields`, `hip_get_current_values`, `hip_compare_expected_actual`, `hip_get_safe_actions`, `hip_verify_action_effect`, `hip_get_route_identity`, and `hip_get_form_generation`.
- Locator fusion reports Playwright accessibility, DevTools DOM, learned HIP fingerprint memory, local DOM semantics, HIP Intelligence consensus, and ambiguity-only vision evidence. Confidence policy: >=0.90 execute; 0.75-0.89 re-observe; 0.55-0.74 explicit rediscovery/self-heal; below 0.55 block.
- Control identity is semantic/fingerprint based; positional/generation-volatile selectors are not trusted across Angular/DDS rerenders.
- MutationObserver telemetry now classifies dialog/drawer/listbox/row/spinner lifecycle, accordion changes, field enable/disable/read-only changes, and SPA route changes in addition to DOM generation.
- Action semantics classify controls as SAFE, CONDITIONAL, or DANGEROUS and feed the existing mission mutation-authorization gate. Create/Save/Submit/Update/Finish/Deploy remain authorization-gated; Delete/Remove/Overwrite/Purge/Destroy/Reset/Revoke are destructive.
- Gemma vision is called only when DOM/accessibility evidence is ambiguous and never supplies coordinates or independently authorizes a click.
- Existing specialized DDS/Material dropdown handling remains; structured form fill is reserved for ordinary verified text controls.
- No Selenium MCP, Puppeteer MCP, second generic browser executor, TypeScript MCP, permanent accessibility executor, or coordinate-only primary agent was added.
- Live GO/NO-GO fails closed if Playwright MCP lacks `browser_find`/`browser_fill_form` or if any required HIP Intelligence semantic tool is missing.

## v2.1.4 semantic website understanding / multi-evidence action gate

- Adds stable value-free semantic control IDs (`SC-XXXXXXXXXXXX`) derived from label/role/section/framework identity rather than customer values or DOM position.
- Fuses the reviewed deterministic locator with Playwright MCP accessibility evidence, Chrome DevTools MCP DOM evidence, HIP Intelligence MCP semantic ranking/critique, validated control-fingerprint memory and Gemma vision only as an ambiguity tie-breaker.
- Requires a confidence threshold and runner-up margin; ambiguous controls fail closed instead of allowing an LLM to click something plausible.
- Revalidates the semantic fingerprint after Angular/DDS rerenders immediately before physical dispatch.
- Verifies exact before/after semantic effects and learns only successful value-free control/state transitions.
- Extends the gate through ordinary click/fill/search actions, DDS single/multi-select, checkbox/radio/toggle controls, tab navigation, repeatable-row `+ Add`, structural-parent exploration and file-upload inputs (including hidden `input[type=file]` controls).
- Expands the existing HIP Intelligence MCP with semantic resolve/rank/fingerprint/effect/capability tools; no extra TypeScript/generic browser MCP is added.
- Live GO/NO-GO now requires the HIP Intelligence MCP semantic toolset in addition to Playwright MCP and Chrome DevTools MCP.
- Mission Trace and the JavaScript Control Center show semantic control ID, confidence, ambiguity status, executor, revalidation and post-action effect evidence.
- Full source regression at promotion: 849/849 PASS. Live Dell SSO/HIP tenant proof still requires the Dell Windows environment.

## v2.1.3 live witness / first Dell tenant test

- The JavaScript Control Center defaults the first live run to **Live witness mode (no mutation controls)**.
- Run sequence: static preflight -> text model test -> vision model test -> Live GO/NO-GO -> Start live witness.
- The witness uses the real Dell SSO session and the integrated AutoWebGLM -> Playwright MCP execution path across all selected P01-P07 phases.
- It may open `+ Add`, expand rows, choose dropdown values, fill the supplied form values, take screenshots, run judges and perform deterministic phase handoffs.
- It never clicks Create/Save/Submit/Finish/Deploy/Delete-style controls. Submit-request capture is disabled even though standard AgentQ autonomous missions normally enable it.
- API mode is forced to `capture`; API write and mutation authorization are rejected by both the UI and backend.
- The readiness fingerprint includes `execution_profile=live_witness`, preventing a standard readiness receipt from being reused for a witness run.
- At the end, `live_witness_certificate.json` independently scans action/network evidence. Any prohibited mutation-control click or mutation-classified request fails the witness and prevents a false complete verdict.
- After a witness passes in the real Dell environment, disable witness mode only when you intentionally want to move to the governed full execution path.
- Current automated regression at promotion: 839/839 PASS. The final tenant proof must still be generated on the Dell Windows machine because this build environment cannot access Dell SSO/HIP.

## v2.1.2 live GO/NO-GO readiness gate

- Adds a non-invasive live readiness run that must pass before a normal mission can start.
- Proves the exact selected input/phase contract, AutoGen 0.7.5, AutoWebGLM primary policy, browser launchability, official Playwright MCP required action tools, Chrome DevTools MCP judge tools, Dell AIA text model, Dell AIA vision image understanding, writable safe-I/O evidence path, and absence of a conflicting mission process.
- Issues a short-lived server-side readiness receipt bound to config path, input JSON, runs directory, golden/upload paths, selected phases, and API mode. Any change invalidates the receipt.
- `/api/mission/start` rejects missing, expired, mismatched, or stale receipts with HTTP 412, so the UI cannot be bypassed by a direct start API call.
- The JavaScript Start button remains disabled until the live gate returns GO; Start also re-runs the live gate automatically if the user has not run it manually.
- Tightens MCP readiness: a server process starting is not sufficient. Playwright MCP must publish navigate/snapshot/click/type/select/screenshot tools and Chrome DevTools MCP must publish snapshot/network/console tools.
- Static preflight now uses the config path selected in the UI rather than silently falling back to `config.yaml`.
- AutoWebGLM remains the planner; PyAutoGUI MCP is the primary physical interaction engine, with official Playwright MCP as deterministic fallback/verification.
- Current source regression at promotion: 830/830 PASS. Live Dell SSO/HIP validation still requires the Dell Windows environment.

## v2.1.1 all-phase transition coordinator

- Computes the next executable selected phase and skips already complete/resumed phases without opening their HIP modules.
- Persists a crash-safe pending handoff and requires the destination phase to acknowledge it only after route verification and dual-MCP agreement.
- A failed handoff never replays the completed source phase; the destination owns bounded route recovery.
- Resume adoption now snapshots the exact-state lock, section judge, verification payload and mission-assurance evidence into the current run so certification does not depend on the old run directory.
- An assured current mission cannot adopt a non-assured source phase.
- Final application-complete status is gated by current-run proof for every selected phase and by the absence of any unacknowledged transition.
- AutoWebGLM remains the primary decision framework; PyAutoGUI MCP is primary for physical interactions and official Playwright MCP remains primary for governed URL navigation plus deterministic action fallback/verification.
- Current automated regression: 822/822 PASS before packaging. Live Dell tenant/SSO validation still requires the Dell Windows environment.

## v2.1.0 completion-first mission hardening

- Windows `[WinError 206]`: directory creation and evidence writes use long-path-safe I/O; generated run/self-heal folders are compact.
- Data Maps operational runs are completion-first/current-input-branch-only. AutoWebGLM recovery is observation-only when no vetted expected intent exists; it cannot "click something plausible" to escape a stall.
- Browser startup is Edge -> installed Chrome -> Playwright Chromium before SSO. The selected browser is locked after authentication.
- PyAutoGUI MCP is the primary physical executor for vetted actions; official Playwright MCP is deterministic fallback/verification and Python Playwright is the final DDS compatibility fallback.
- Text and vision model health can be probed independently from the UI/backend without starting a mission.
- Live mission trace uses stable P01-DM through P07-BF identifiers and records what was observed, filled, clicked, executed, verified and blocked, including planner/executor provenance.
- MCP/CDP transport recovery reattaches both MCP clients to the same authenticated browser/tab and never switches browsers mid-mission.
- Active no-progress watchdog fingerprints structural control state without storing customer values. Repeated A->B->A UI cycles are interrupted and sent to bounded deterministic recovery.
- A completed phase actively hands off to the next route using Playwright MCP-first navigation, then requires Python Playwright + Playwright MCP + Chrome DevTools MCP agreement before the next phase begins.
- Current automated regression: 813/813 PASS before packaging. Live Dell tenant/SSO validation still requires the Dell Windows environment.

## v2.0.0 cross-layer convergence hardening

- Mutation success evidence is now fenced to requests first observed after the exact physical dispatch boundary on the same HIP route/stage; preflight/background writes cannot falsely prove a requested change.
- A browser-session mutation quarantine is armed at dispatch and blocks every other mutation until authoritative reconciliation clears it. Ambiguous/partial/response-lost states keep the quarantine active.
- Governed execution writes a persistent `change_execution_started` event. If a process crashes or an outcome remains indeterminate, an identical mutation is quarantined across later runs and `force_repeat_mutation` cannot bypass that unresolved state.
- Governance ledger appends are cross-process serialized and fsynced so parallel agents cannot fork the hash chain.
- Immediately before any mutation dispatch, the runtime rechecks SSO, exact HIP route and universal locator preflight after the AutoWebGLM decision. Drift here is classified as pre-dispatch and cannot reach the backend.
- Nested menu/dialog/drawer ancestry is route-bound. Angular SPA navigation to another HIP module invalidates the complete surface chain rather than allowing stale child provenance to survive.
- Runtime self-heal treats all certified mutation quarantine/rejection/ambiguous/no-retry outcomes as `unsafe_or_mutating`, so a generic timeout recovery cannot replay a mutation phase.
- These guards are additive to the v1.9.2-v1.9.7 protections: semantic row identity, compound actions, detached-overlay provenance, virtualized options, continuous leases, nested ancestry and mutation outcome reconciliation remain active.



## v1.9.7 mutation outcome reconciliation + no duplicate dispatch guard

- Mutation clicks now carry explicit physical-dispatch evidence (`pre_dispatch`, `dispatching`, `dispatch_returned`, `dispatch_exception`) so a locator/preflight failure is no longer confused with a backend-ambiguous click.
- After any physical mutation dispatch is attempted, Playwright MCP -> local Playwright -> PyAutoGUI fallback is disabled for that action. Read-only actions keep their normal recovery behavior.
- A bounded read-only reconciliation window observes HIP write responses and value-free visible status signals without issuing another mutation.
- A captured 2xx write response can recover a mutation as committed even when the browser tool times out after the click.
- A terminal non-2xx write response is classified as `rejected_verified`; no automatic retry occurs.
- A visible success indicator without an authoritative 2xx response is preserved as `visible_success_network_unconfirmed` and requires review rather than a second click.
- Mixed successful/failed write responses are treated as partial/ambiguous changes and fail closed.
- Only a provable pre-dispatch failure may perform one fresh semantic rebind, because no physical mutation was sent. This is not a backend mutation retry.
- Reconciliation evidence stores structural status/hashes only for UI signals; raw toast text and customer-entered values are not persisted.
- AutoWebGLM, AgentQ, mutation authorization, nested surface ancestry, exact post-action verification and duplicate-success governance remain intact.


## v1.9.6 nested surface ancestry chain + continuation scope guard

- A proven action can now open a second or third detached surface without losing its original entity/row provenance. The runtime records a bounded structural ancestry chain from the exact opener through each child surface.
- Continuation actions (`Next`, `Back`, `Save`, `Create`, `Close`, `Edit`, `Clone`, `Migrate`, `Deploy`, `Delete`, `Upload`, `Download`, `Retry`) are resolved inside the deepest still-proven child first.
- If the active child is valid but the requested continuation action is absent, execution fails closed. It does not search the whole page for a same-named global action.
- When a child surface closes, only that child is pruned; the still-visible parent is re-proven and becomes active again.
- Surface ancestry is reset on module navigation, capped at eight levels, and stores structural evidence only—never customer-entered values.
- AutoWebGLM remains the primary browser-decision layer; AgentQ, mutation governance, exact effect verification, Edge session ownership, v1.9.5 surface leases and prior semantic-affordance protections remain intact.


## v1.9.5 continuous overlay provenance lease + stable target guard

- A detached Angular/CDK/DDS menu is no longer trusted because it was correct once. Its provenance is refreshed before every semantic read, virtualized scroll, and final click.
- If Angular destroys and recreates the overlay while lazy-loading or filtering, trust transfers only when `aria-controls`/`aria-owns` or one unique structural continuation proves the replacement.
- Selector equality is only a weak continuity hint. Two stale/current surfaces with comparable evidence fail closed instead of choosing by DOM position or z-index alone.
- A target such as `Deploy` must resolve consistently across consecutive reads inside the same proven surface before it is eligible to execute.
- Immediately before click, the runtime proves there is exactly one visible matching target and that it is contained by exactly one currently proven surface. A global duplicate `Deploy`/`Edit` therefore blocks rather than being clicked.
- AutoWebGLM remains the primary decision layer; AgentQ, mutation governance, effect verification, Edge session ownership, vision recovery and the previous row/compound/virtualized protections remain unchanged.


## v1.9.4 detached overlay provenance + virtualized option surfaces

- A correctly scoped row action such as `UHAL -> ⋮` can open a menu under `document.body`/CDK overlay containers without retaining UHAL in DOM ancestry. v1.9.4 transfers trust only to the exact newly opened actionable surface.
- `aria-controls` / `aria-owns` is preferred when present; otherwise a single unambiguous new-surface delta is required. Equal-confidence multiple overlays fail closed.
- `Edit`, `Clone`, `Migrate`, `Deploy`, `Delete` and `Download` are then resolved only inside the proven surface, preventing a similarly named global action from being selected.
- Long/virtualized menus are searched by bounded scroll-only discovery inside the proven overlay. Scrolling cannot execute a mutation.
- The target is re-resolved inside the same surface immediately before the existing `BrowserSession.click_and_wait` path, so AutoWebGLM, AgentQ, Playwright/DDS verification and mutation governance remain in force.

## v1.8.7 Microsoft Edge + multimodal vision + five-minute loading recovery

- Microsoft Edge Stable (`portal.chromium_channel: "msedge"`) is the primary persistent HIP browser. Playwright MCP, Chrome DevTools MCP (via CDP), Browser Use, LangChain and AutoWebGLM all observe the same authenticated Edge session rather than launching competing browsers.
- The live controller uses a Dell AIA multimodal vision deployment during recovery; the strict section judge continues to require a final visual decision before a section can pass.
- A true blocking loading surface must persist continuously for **more than five minutes** and be independently confirmed by the vision model before a page refresh is allowed. Passive DDS spinners/aria-busy markers are ignored.
- If the five-minute refresh occurs while an unsaved form is open, no customer values are checkpointed; the phase is reopened and deterministically replayed from the current `input.json`.
- AutoWebGLM runtime compatibility now includes task description, simplified HTML, viewport position, previous actions and all **10 official action types** (`click`, `hover`, `select`, `type_string`, `scroll_page`, `go`, `jump_to`, `switch_tab`, `user_input`, `finish`). Research training/benchmark datasets and ChatGLM3-6B weights remain optional external assets rather than production runtime dependencies.

## v1.8.6 adaptive browser-intelligence recovery

- Normal selected-phase planning context increased from 18k to **64k characters**.
- Failure-only recovery context is independently bounded at **128k characters**; the Dell AIA self-heal advisor may receive up to **96k characters**.
- AutoWebGLM-style observation/action recovery is integrated on the existing HIP page (task + simplified value-free HTML + viewport + action history -> one bounded proposal).
- The original AutoWebGLM 6B checkpoint is optional and is not bundled; `native_model_command` can connect an approved wrapper, while Dell AIA is the default protocol planner.
- `langchain-community==0.4.2` Playwright Browser Toolkit is attached to the same CDP browser in **read-only recovery mode**. Navigation/click tools are filtered out.
- Browser Use state budget is 60k and remains semantic/recovery-only. AutoWebGLM may use up to 70k of simplified HTML and 40 previous actions.
- Every new browser-intelligence call is timeout-bounded and cannot bypass AgentQ reward gating, deterministic field mapping, exact post-action verification, or mutation governance.

## v1.8.5 progress-driven learning + expert-skill architecture

### v1.8.5 live Document Type / learning hardening

- Document Type is deterministic-first when the reviewed Unified Deep KB is available; exhaustive dropdown discovery is recovery-only.
- A judged Source Document Type success is replayed as value-free same-family structural memory for Target Document Type, so the target does not rediscover the same form.
- `--runtime-self-heal-until-complete` is progress-driven: repeated identical no-progress failures and a configurable per-phase wall clock are always enforced.
- Capabilities and API Contracts are bootstrapped from the reviewed Unified Deep KB and then updated by candidate runtime plans/live network observations even if a phase later fails.
- AgentQ action selection now uses persistent transition reward, success rate, bounded UCB exploration and repeated-loser suppression; MCP advice cannot bypass the local reward/safety gate.
- Failure forensics use the existing authenticated Chrome page to capture a value-free semantic DOM probe plus screenshot. No second headless browser is launched.

See `DOCUMENT_TYPE_STALL_MEMORY_AGENTQ_FIX_20260901.md` for the detailed root cause and controls.

The agent now applies five execution principles directly in runtime:

1. **The description is the trigger.** A natural-language mission description is deterministically mapped to the exact HIP phase skill(s). `full/end-to-end` maps to all seven phases; `Transport Profile only` maps to Source + Target TP; multi-family descriptions can map to an exact custom phase subset.
2. **Built from real expertise.** Every HIP phase has a registered expert skill backed by the existing phase-specific deterministic implementation, learned KB, Portal Brain/capability graph and exact verification contract.
3. **Spend context wisely.** Preflight builds a phase-local context budget containing only selected input branches and bounded verified capabilities. Broad portal state is reserved for recovery.
4. **Use deterministic scripts.** Phase-specific scripts remain the action authority. Browser Use, MCP and PyAutoGUI are recovery layers, not substitutes for deterministic field mapping.
5. **Vet a skill before running it.** The backend refuses to launch a browser mission when a selected expert skill implementation is missing or its selected-phase input preflight fails.

Browser Use 0.13.8 now contributes a compact same-CDP recovery context (URL/title/tabs plus a bounded semantic inventory of actionable elements) instead of returning the whole browser state tree to every recovery step. It remains perception/recovery-only; state-changing HIP actions stay under deterministic Playwright/MCP/governance.

The JavaScript UI exposes a **Description trigger**, **Expert skill vetting** and **Context budget** panel before execution.



## v1.8.3 exact-fill action ladder

Every resolved HIP control now uses the same bounded execution ladder:

```text
Playwright MCP
    -> deterministic Python Playwright
    -> PyAutoGUI last-resort physical interaction (Windows, visible control only)
    -> exact/stable DOM verification
```

PyAutoGUI never discovers a target, never clicks arbitrary screen coordinates and cannot perform final Save/Create/Delete/Deploy mutations by default. It receives only an already-resolved unique Playwright locator after normal form-policy and mutation guards have passed. Text fields are also idempotent: if the exact requested value is already committed, the runtime records `already-committed` and does not type it again.

This addresses the gap between *knowing the correct input value* and *proving the live Angular/DDS control actually accepted and retained it*. Dynamic rerenders, overlays, delayed dropdown commits, focus drift and browser/MCP interaction failures are treated as interaction-state problems rather than data problems.


This project automates the **specific ID issue** in the Dell HIP/BizLink Portal flow:

1. Open the Dell BizLink Partner page.
2. Complete Dell SSO using a persistent browser profile.
3. Search/explore the Partner record and extract the required Partner ID.
4. Navigate to the Dell BizLink System page.
5. Search/explore the System record and extract the required System ID.
6. Capture Network-tab style evidence, console logs, screenshots, and click history.
7. Save IDs into local memory.
8. Optionally enrich `input.json` so HIP APIs can run with real IDs.
9. Generate `final_report.json`, `final_report.md`, and `final_report.html`.
10. Build a local Knowledge Graph of the run, including stages, pages, clicks, network events, extracted IDs, registry values, screenshots, and report files.

The runtime uses three complementary MCP servers:

- **PyAutoGUI MCP** — primary visible-desktop click/type/key execution after semantic proof.
- **Playwright MCP** — accessibility/DOM evidence, governed navigation, deterministic fallback and post-action verification.
- **Chrome DevTools MCP** — network, console, performance and low-level browser evidence.
- **HIP Intelligence MCP** — local value-free web representation, action planning, critic, portal-drift and trajectory-memory tools.

Only Playwright MCP and Chrome DevTools MCP attach to the existing authenticated Microsoft Edge context. HIP Intelligence MCP never opens another browser and never stores customer-entered values. The Python code uses installed Microsoft Edge by default and does not require a downloaded Playwright Chromium build.


## Course web-agent architecture integrated

The uploaded DeepLearning.AI course concepts are implemented in HIP-specific form:

```text
User input / input.json
        ↓
Dell AIA GPT-OSS-120B task understanding and section judges
        ↓
Hierarchical phase planner
        ↓
Web Representation Model
  DOM + accessibility + Angular/DDS + events + MCP evidence
        ↓
Action Model with bounded UCB-style search
        ↓
Playwright MCP actor / deterministic DDS drivers
        ↓
Transaction critic and Gemma visual judge
        ↓
Value-free trajectory and flow-pattern memory
```

The AgentQ ideas are used without the MultiOn dependency:

- Planner, actor and critic roles are separated.
- Every field is one sequential task with exact verification.
- Safe candidate actions are ranked using a bounded UCB-style search.
- Successful and failed transitions are remembered.
- Value-free action preference pairs are generated for optional future offline policy tuning.
- A validated prior may increase a control's score, but cannot override live one-to-one binding, active-surface, validation or mutation gates.

Persistent learning is stored under:

```text
data/hip_memory/portal_brain/agentq/action_trajectories/
  trajectories.jsonl
  preference_pairs.jsonl
  manifest.json
```

Per-run evidence includes:

```text
agentq_course_architecture_manifest.json
hip_intelligence_mcp_startup.json
<phase>/agentq_runtime/hierarchical_phase_plan.json
<phase>/agentq_runtime/phase_initial_web_representation.json
<phase>/agentq_runtime/action_model_plan_*.json
<phase>/agentq_runtime/action_critic_*.json
<phase>/agentq_runtime/phase_agentq_summary.json
```


## Autonomous mission completion (2026-07-21)

The all-phase run is now one continuous autonomous objective:

- `mission_state.json` is a crash-safe ledger of every phase (pending / in_progress / complete / blocked).
- `--resume-run <dir>` or `--auto-resume` (PowerShell: `-ResumeRunDir` / `-Resume`) adopts judge-approved completed phases from an interrupted run fail-closed — exact-state lock + passing section-judge gate + persisted verification are all required — and re-executes everything unproven live, without replaying proven phases.
- A dead or crashed Chrome is now classified as `browser_disconnected` and recovered with the safe `restart_browser_session` action: the persistent user-data-dir relaunches, SSO cookies are reused, and the interrupted phase replays deterministically.
- `mission_entity_registry.json` keeps one consistent set of entity names per run and injects `_mission_prior_entities` into later phases; it is value-scoped to the run and never promoted into the portal brain.
- `mission_completion_report.json` / `.md` declare `application_complete: true` only when every phase passed the deterministic, text and vision gates.

See `LIVE_TESTING_README_AUTONOMOUS_MISSION_20260721.md`.

## Command correction for Windows PowerShell

Use the package/module form:

```powershell
python -m hip_id_agent.cli --help
```

Do **not** use relative module names like `python -m .\hip_id_agent\.cli --help`; Python will reject that with `Relative module names not supported`.

See also: `WINDOWS_CHROME_RUN_GUIDE.md`.

---

## Target URLs

The two primary navigation targets are already configured:

```text
https://developer.dell.com/hybrid-integrations/bizlink/partner
https://developer.dell.com/hybrid-integrations/bizlink/system
```

These are used before fallback menu navigation.

---

## What gets captured

Each run writes evidence under `runs/<RUN_ID>/`:

```text
runs/<RUN_ID>/
  final_report.json
  final_report.md
  final_report.html
  enriched_input.json                  # if --input-json is supplied
  screenshots/
    00_after_sso.png
    partner_01_after_navigation.png
    partner_02_after_search.png
    partner_03_after_open_details.png
    system_01_after_navigation.png
    system_02_after_search.png
    system_03_after_open_details.png
  network/
    network_tab_events.json             # CDP Network-tab style events
    network_records_legacy.json          # Playwright response fallback
  clicks/
    click_events.json                    # what automation/user clicked on
  console/
    console_messages.json
  knowledge_graph/
    flow_knowledge_graph.json           # graph nodes/edges for run review and learning
    flow_knowledge_graph.html           # visual Mermaid graph + previews
    flow_knowledge_graph.mmd            # Mermaid source
    click_sequence.json                 # ordered click path with before/after URLs
```

`network_tab_events.json` contains redacted request/response headers and JSON response bodies. Secrets are masked.

`click_events.json` captures:

- automated clicks
- manual/browser DOM clicks where possible
- clicked text
- tag
- id/classes
- href
- bounding box
- URL before the click
- URL after the click for automation-controlled clicks
- current stage/action context

`knowledge_graph/flow_knowledge_graph.json` turns the run into a graph so you can audit and reuse the flow:

- `Run -> HAS_STAGE -> Stage`
- `Stage -> OBSERVED_PAGE -> Page`
- `Stage -> PERFORMED_CLICK -> Click -> TARGETED_ELEMENT -> UIElement`
- `Stage -> OBSERVED_NETWORK_EVENT -> NetworkEvent -> CALLS_ENDPOINT -> Endpoint`
- `Stage -> EXTRACTED_ID -> Partner/System`
- `Customer -> USES_ENTITY -> Partner/System`
- `Run -> REGISTRY_VALUE -> extracted IDs and evidence paths`

---

## Install

```powershell
cd hip_portal_id_agent_full
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# No Chromium download is required. The bundled config uses installed Google Chrome, with Edge as startup fallback.
copy config.example.yaml config.yaml
```

Review `config.yaml`. By default it uses installed Google Chrome Stable, with Edge and Playwright Chromium available only as startup fallbacks:

```yaml
portal:
  chromium_channel: "chrome"
  browser_user_data_dir: "./data/chrome_profile"
  fallback_to_chrome: true
  fallback_to_edge: true
  fallback_to_playwright_chromium: true
```

If Chrome channel lookup fails on your corporate image, set the full path instead:

```yaml
portal:
  chromium_channel: null
  chrome_executable_path: "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

If Dell SSO is explicitly required to use Edge, override the primary channel:

```yaml
portal:
  chromium_channel: "msedge"
  browser_user_data_dir: "./data/edge_profile"
```

---

## Run ID extraction

```powershell
python -m hip_id_agent.cli extract-ids `
  --config config.yaml `
  --customer UHAL `
  --partner-query UHAL `
  --system-query UHAL
```

The browser opens. Complete Dell SSO if prompted. The agent then navigates to Partner and System pages.

---

## Run and enrich input.json

```powershell
python -m hip_id_agent.cli extract-ids `
  --config config.yaml `
  --customer UHAL `
  --partner-query UHAL `
  --system-query UHAL `
  --input-json .\customer\UHAL-POASN\input.json
```

This writes:

```text
runs/<RUN_ID>/enriched_input.json
```

The enrichment replaces empty/placeholder fields such as:

- `partner_id`
- `partnerId`
- `partnerIdentifierId`
- `system_id`
- `systemId`
- `domainId`
- `account_id`

It also replaces placeholder strings:

```text
${partner_id}
{{partner_id}}
${system_id}
{{system_id}}
```

---

## Knowledge Graph review

After each run, open:

```text
runs/<RUN_ID>/knowledge_graph/flow_knowledge_graph.html
runs/<RUN_ID>/knowledge_graph/flow_knowledge_graph.json
runs/<RUN_ID>/knowledge_graph/click_sequence.json
```

CLI graph summary:

```powershell
python -m hip_id_agent.cli graph-summary .\runs\<RUN_ID>
```

Use this to verify:

- which Partner/System page was reached
- exactly which element was clicked
- which click opened a detail page
- which API/network response produced the ID
- whether the ID came from network JSON, table, visible text, URL, or AIA fallback
- how the Partner/System IDs were stored for future API runs

The graph is stored locally only. No additional browser MCP server is created; the included HIP Intelligence MCP is local, value-free, and browserless.

---

## Optional API execution

Keep dry-run until you verify the enriched payload.

```yaml
api:
  enabled: true
  dry_run: true
  base_url: "https://REAL-HIP-API-BASE"
  token_env_var: "HIP_API_TOKEN"
  requester_id: "YOUR_REQUESTER"
  account_id: "YOUR_ACCOUNT_ID"
  requests:
    - name: "validate_transport_profile"
      method: "POST"
      path: "/path/to/validate"
      body_path: "transportProfile"
```

Then run:

```powershell
python -m hip_id_agent.cli extract-ids `
  --config config.yaml `
  --customer UHAL `
  --partner-query UHAL `
  --system-query UHAL `
  --input-json .\customer\UHAL-POASN\input.json `
  --run-api
```

---

## AIA model fallback

Normal extraction does **not** need AIA. The deterministic extractor checks:

1. Network-tab JSON responses
2. Playwright response JSON
3. Tables
4. Visible key-value text
5. URLs and links

AIA fallback is only called when deterministic confidence is low.

To enable Dell AIA with `aia_auth`:

```yaml
aia:
  enabled: true
  auth_mode: "auto"
  use_aia_auth_package: true
  model: "gpt-oss-120b"
```

Then set one of these:

```powershell
$env:AIA_ENDPOINT="https://.../chat/completions"
# or
$env:OPENAI_BASE_URL="https://..."
```

For client credentials:

```powershell
$env:CLIENT_ID="..."
$env:CLIENT_SECRET="..."
```

For SSO token via `aia_auth.auth.sso()`, just keep `auth_mode: auto` without client credentials.

---

## MCP usage guidance

This repo uses the existing Playwright and Chrome DevTools browser MCPs and includes one local browserless HIP Intelligence MCP. Use them like this:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    },
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest", "--no-usage-statistics"]
    },
    "browsermcp": {
      "command": "npx",
      "args": ["@browsermcp/mcp@latest"]
    }
  }
}
```

Recommended usage:

```text
Playwright MCP       -> observe browser, snapshots, screenshots, form state
Chrome DevTools MCP  -> inspect Network tab, console, failed requests, generated IDs
Browser MCP          -> reuse existing logged-in browser session if SSO blocks isolated Playwright
```

The Python app also captures the Network tab internally through CDP so the report is created even if you do not manually inspect DevTools.

### Chrome DevTools MCP is now implemented

This project now includes a real stdio adapter for the pre-existing `chrome-devtools-mcp` server. It does **not** create a custom MCP server.

Validate your local Chrome DevTools MCP runtime first:

```powershell
python -m hip_id_agent.cli chrome-devtools-check --config config.yaml
```

To force the experimental direct MCP backend instead of Playwright, configure:

```yaml
mcp:
  browser_backend: "mcp"
  use_chrome_devtools_mcp: true
  chrome_devtools_command: "npx"
  chrome_devtools_args:
    - "-y"
    - "chrome-devtools-mcp@latest"
    - "--no-usage-statistics"
  chrome_devtools_mcp_direct_backend_enabled: true
```

Behavior:

```text
playwright -> uses local Playwright with CDP network capture
auto       -> records MCP attempt and falls back to Playwright
mcp        -> uses Chrome DevTools MCP only when direct backend is enabled; otherwise fails clearly
```

Playwright remains the recommended default for Dell SSO and stable DOM/table handling. Chrome DevTools MCP is available for direct MCP execution/probing and additional DevTools-oriented automation where your runtime has `npx` and the pre-existing MCP package available.

---

## Show memory

```powershell
python -m hip_id_agent.cli show-memory --config config.yaml --customer UHAL
```

Memory files:

```text
data/hip_memory/object_registry.json
data/hip_memory/partners.json
data/hip_memory/systems.json
data/hip_memory/entity_registry_index.json
```

---

## Network summary

```powershell
python -m hip_id_agent.cli network-summary runs\UHAL-YYYYMMDD-HHMMSS
```

Filter example:

```powershell
python -m hip_id_agent.cli network-summary runs\UHAL-YYYYMMDD-HHMMSS --contains partner
```

---

## Troubleshooting

### SSO does not complete

- Run in non-headless mode.
- Complete SSO manually in the opened browser.
- Keep `data/chrome_profile` so the next run reuses the Chrome SSO session.
- Default browser is installed Google Chrome: `portal.chromium_channel: "chrome"`. Edge is the first startup-only fallback. No `python -m playwright install chromium` is required when Chrome/Edge is available.
- If Chrome channel lookup fails, set `portal.chrome_executable_path` to the full `chrome.exe` path; if Chrome cannot start or CDP is unhealthy, the configured startup fallback may use Edge.

### Partner/System page opens but search does not work

The agent still extracts from visible tables and network calls. Check:

```text
runs/<RUN_ID>/screenshots/
runs/<RUN_ID>/network/network_tab_events.json
runs/<RUN_ID>/clicks/click_events.json
```

### ID is not extracted

Open `final_report.html` and review candidate debug data. If the ID is visible in a different key name, add it to `FIELD_MAP` in `hip_id_agent/id_extractor.py`.

### Network tab is empty

- The page may have loaded data before CDP was attached. Re-run after SSO is already completed.
- Try clearing/re-searching the table.
- Keep non-headless mode so browser events are observable.

---

## Senior AI architect notes

The architecture separates the concerns:

```text
SSO/session/browser      -> BrowserSession
Navigation/exploration  -> PageExplorer
Network/UI ID parsing   -> IDExtractor
Dell AIA fallback       -> AIAClient
Persistence             -> HipMemory
Payload enrichment      -> HipAPIClient
Evidence reporting      -> ReportWriter
CLI orchestration       -> PartnerSystemIDFlow + cli.py
```

This prevents the agent from guessing IDs and creates an auditable path from browser evidence to API-ready payload.

---

## v3 hardening notes

This package now implements strict Partner/System ID validation and an expanded Knowledge Graph.

Key behavior:

- A generic `id` is not trusted unless endpoint context and entity/name evidence support it.
- Typed fields such as `partnerId`, `systemId`, and `domainId` are strongly preferred.
- Conflicting verified candidates are marked ambiguous and are not saved automatically.
- `enriched_input.json` is the real executable file and is not masked.
- `enriched_input.redacted.json` is the safe reporting copy.
- Reports and KG labels are redacted and should reference the redacted file.
- Every run writes `click_sequence.json`, `action_sequence.json`, `network_events.jsonl`, `network_summary.json`, `id_candidates.json`, `verified_ids.json`, `rejected_ids.json`, and `recovery_suggestions.json`.
- Failure runs still produce `failure_bundle/` with the last screenshot, DOM, actions, network, console logs, and recovery suggestions.
- No additional browser-controller MCP is built. The project uses existing Playwright/Chrome DevTools MCPs plus the local browserless HIP Intelligence MCP; direct Python Playwright remains the bounded DDS fallback.

See:

- `CHANGELOG.md`
- `IMPLEMENTATION_VERDICT.md`
- `ACCEPTANCE_TEST_RESULTS.md`


## Broad Partner/System Discovery and Safe Button Exploration

To search one example first, then safely explore visible Partner/System buttons and menus:

```powershell
python -m hip_id_agent.cli extract-ids `
  --config .\config.yaml `
  --customer UHAL `
  --partner-query AS2TEST `
  --system-query AIC-DCE `
  --explore-all-buttons `
  --collect-all-pages `
  --max-pages 10 `
  --max-total-actions 160
```

To crawl Partner and System pages broadly, including multiple pages, without requiring one exact input JSON:

```powershell
python -m hip_id_agent.cli discover-portal `
  --config .\config.yaml `
  --customer DISCOVERY `
  --partner-query AS2TEST `
  --system-query AIC-DCE `
  --collect-all-pages `
  --max-pages 10
```

The exploration mode is read-only by default. It opens safe actions such as View, Show, Details, Open, and Edit pages only for evidence capture. It skips Delete, Update, Save, Submit, Add, Create, Reset, Enable, Disable, and Remove unless `--allow-unsafe-clicks` is explicitly set.

Outputs include:

```text
runs/<RUN_ID>/portal_exploration.json
runs/<RUN_ID>/network_events.jsonl
runs/<RUN_ID>/action_sequence.json
runs/<RUN_ID>/knowledge_graph/flow_knowledge_graph.html
runs/<RUN_ID>/report.html
```

## Full inventory export: all Accounts, Partners, Domains and Systems

Use this when you do not want only one Partner/System ID. It uses the authenticated browser session to call the same read-only APIs seen in the Network tab and fans out across every parent Account/Domain. This is more complete than clicking only visible cards or relying on top-level search.

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

Outputs are written under:

```text
runs\<RUN_ID>\inventory\all_entities.json
runs\<RUN_ID>\inventory\all_entities.csv
runs\<RUN_ID>\inventory\accounts.json
runs\<RUN_ID>\inventory\partners.json
runs\<RUN_ID>\inventory\domains.json
runs\<RUN_ID>\inventory\systems.json
runs\<RUN_ID>\inventory\relationships.json
runs\<RUN_ID>\inventory\relationships.csv
```

The local memory is also updated:

```text
data\hip_memory\accounts.json
data\hip_memory\partners.json
data\hip_memory\domains.json
data\hip_memory\systems.json
```

The command is read-only. It does not create/update/delete anything. It uses GET calls for Accounts, Partners, Domains and Systems and preserves parent relationships like Account → Partner and Domain → System.

### Full inventory export note

`export-all` now replays the same authenticated `x-requester-id` header seen in the browser Network tab. This is required by Dell BizLink gateways; otherwise direct calls like `/authz/accounts` can return HTTP 400 and Accounts/Partners become zero.

Run:

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

If your enterprise profile does not expose requester context automatically, set this in `config.yaml`:

```yaml
api:
  requester_id: "your.name@dellteam.com"
```


## Full inventory audit/retry mode

For full Accounts, Partners, Domains and Systems export, run:

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

After the run, review:

```text
runs\<RUN_ID>\inventoryccounts.json
runs\<RUN_ID>\inventory\partners.json
runs\<RUN_ID>\inventory\domains.json
runs\<RUN_ID>\inventory\systems.json
runs\<RUN_ID>\inventory
elationships.json
runs\<RUN_ID>\inventory\inventory_request_audit.json
runs\<RUN_ID>\inventory
ailed_requests.json
```

If `failed_requests.json` is not empty, the export is partial. The agent retries transient gateway errors and then attempts read-only UI fallback, but unresolved 500/permission errors still need a rerun or portal/API fix.

### Full inventory status meaning

`export-all` can finish in three states:

- `success`: all Account→Partner and Domain→System fan-out calls succeeded.
- `partial_success`: inventory files were exported, but one or more read-only Dell gateway calls still returned a non-200 response after retries. Check `runs/<RUN_ID>/inventory/failed_requests.json`.
- `failed`: no useful inventory stage completed.

For live Dell 500 errors, the exported counts are still available in `inventory_counts` and the unresolved parents are listed in `failed_requests.json`.

## Full detail inventory export

Use `export-all` when you need every Account, Partner, Domain and System detail record from BizLink.

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

Important outputs:

```text
runs\<RUN_ID>\inventory\accounts.json
runs\<RUN_ID>\inventory\partners.json
runs\<RUN_ID>\inventory\domains.json
runs\<RUN_ID>\inventory\systems.json
runs\<RUN_ID>\inventory\accounts_with_partners_details.json
runs\<RUN_ID>\inventory\domains_with_systems_details.json
runs\<RUN_ID>\inventory\complete_inventory_tree.json
runs\<RUN_ID>\inventory\relationships.json
runs\<RUN_ID>\inventory\inventory_request_audit.json
runs\<RUN_ID>\inventory\failed_requests.json
```

The detail-tree outputs mirror the portal flow:

```text
Account -> Show Partner(s) -> Partner detail rows
Domain  -> View Domain/System(s) -> System detail rows
```

For speed and reliability, the tool first uses the same Network endpoints the UI uses. If a parent fan-out call returns a live Dell gateway error, it attempts a targeted read-only UI fallback for that exact parent card. The fallback searches only for the parent Account/Domain name; it does not search child Partner/System names in the top-level parent page.


## Full Partner/System API ID inventory

Run:

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

Important outputs:

```text
runs\<RUN_ID>\inventory\complete_inventory_tree.json
runs\<RUN_ID>\inventoryccounts_with_partners_details.json
runs\<RUN_ID>\inventory\domains_with_systems_details.json
runs\<RUN_ID>\inventory\deployment_groups.json
runs\<RUN_ID>\inventorypi_required_ids_candidates.json
runs\<RUN_ID>\inventorypi_required_ids_candidates.csv
runs\<RUN_ID>\inventorypi_id_lookup_by_name.json
```

`api_required_ids_candidates.json` maps the collected Partner/System inventory into the API values needed before TP/Flow work: `accountId`, `partner_id`, `domainId`, `source_system_id`, `target_system_id`, `source_deployment_group_id`, and `target_deployment_group_id` candidates.

## Full Partner/System API-ID inventory

Use this when you need all IDs available from the BizLink Partner and System links before moving to TP/Flow APIs:

```powershell
python -m hip_id_agent.cli export-all `
  --config .\config.yaml `
  --customer FULL-INVENTORY `
  --max-pages 25 `
  --max-total-actions 1000
```

By default this performs both:

- API fan-out from Network-tab endpoints.
- Exhaustive read-only UI nested walk:
  - every Account → Show Partner(s)
  - every Domain → View Domain/System(s)

Useful outputs:

```text
runs\<RUN_ID>\inventory\accounts.json
runs\<RUN_ID>\inventory\partners.json
runs\<RUN_ID>\inventory\domains.json
runs\<RUN_ID>\inventory\systems.json
runs\<RUN_ID>\inventory\deployment_groups.json
runs\<RUN_ID>\inventory\complete_inventory_tree.json
runs\<RUN_ID>\inventory\api_required_ids_candidates.json
runs\<RUN_ID>\inventory\api_id_lookup_by_name.json
runs\<RUN_ID>\inventory\relationships.json
runs\<RUN_ID>\inventory\failed_requests.json
```

To run API-only, use:

```powershell
python -m hip_id_agent.cli export-all --no-full-ui-nested-walk
```

### Export progress / stuck-run visibility

`export-all` now prints a progress bar while it runs. It shows the active phase, completed/total parent objects, percent complete, current parent name, and running counts for accounts, partners, domains, systems, and deployment groups.

Progress is also written continuously to:

```text
runs/<RUN_ID>/inventory/progress.json
runs/<RUN_ID>/inventory/progress_events.jsonl
runs/<RUN_ID>/inventory/progress_heartbeat.txt
```

Use `progress.json` for the latest snapshot. Use `progress_events.jsonl` to audit each step. If `progress_heartbeat.txt` does not change for several minutes, the browser is likely waiting on SSO, a Dell gateway response, or a UI blocker.

### Export-all stuck/finalizing watchdog
If `export-all` reaches `finalizing | Flushing browser/network logs`, the run now keeps updating `inventory/progress.json` and will not wait forever. Large Network captures are streamed to disk. If Dell gateway/API calls hang, they time out and appear in `inventory/failed_requests.json` / `inventory_request_audit.json`.

### Small upload summary for large inventory runs

`export-all` now creates a small review pack automatically:

```text
runs/<RUN_ID>/UPLOAD_THIS_SUMMARY.zip
runs/<RUN_ID>/upload_summary/run_summary_for_review.json
runs/<RUN_ID>/upload_summary/completion_summary.md
runs/<RUN_ID>/upload_summary/partner_system_api_id_inventory_compact.csv
runs/<RUN_ID>/upload_summary/failed_requests_summary.csv
```

For an existing large run, create the summary without rerunning:

```powershell
python -m hip_id_agent.cli summarize-run .\runs\FULL-INVENTORY-20260705-223105
```

By default, `export-all` keeps raw evidence compact. To write full raw Network/KG evidence only when required:

```powershell
$env:HIP_WRITE_FULL_NETWORK_LOGS="1"
$env:HIP_WRITE_FULL_KG="1"
python -m hip_id_agent.cli export-all --config .\config.yaml --customer FULL-INVENTORY --write-heavy-evidence
```


## SecureLink Data Map KB discovery

After Partner/System inventory is complete, use this command to learn the SecureLink Data Maps `+ Add` form without saving anything:

```powershell
python -m hip_id_agent.cli discover-datamap-kb `
  --config .\config.yaml `
  --customer DATAMAP-KB `
  --input-json .\examples\uhaul_datamap_dummy_input.json `
  --known-map-id 1670
```

The command opens:

```text
https://developer.dell.com/hybrid-integrations/securelink/datamaps
```

It clicks `+ Add`, captures all visible controls, required fields, dropdown options, button selectors, DOM event hints, and compact network evidence. It fills disposable dummy values but never clicks `Save`, `Create`, or `Submit`.

Upload only this small summary when asking for review:

```text
runs\<RUN_ID>\UPLOAD_DATAMAP_KB_SUMMARY.zip
```

Generated files include:

```text
runs\<RUN_ID>\datamap_kb\datamap_form_kb.json
runs\<RUN_ID>\datamap_kb\DATAMAP_KB_SUMMARY.md
runs\<RUN_ID>\datamap_kb\datamap_dropdowns.json
runs\<RUN_ID>\datamap_kb\datamap_required_fields.json
runs\<RUN_ID>\datamap_kb\datamap_dom_events.json
runs\<RUN_ID>\datamap_kb\datamap_dummy_fill_plan.json
```

### Data Map API + Form Knowledge Base

Use this command to learn the SecureLink Data Maps page end to end without saving anything:

```powershell
python -m hip_id_agent.cli discover-datamap-kb `
  --config .\config.yaml `
  --customer DATAMAP-KB `
  --input-json .\examples\uhaul_datamap_dummy_input.json `
  --known-map-id 1670 `
  --crawl-old-datamaps `
  --max-api-pages 25
```

It first captures the old Data Maps listing APIs and extracts existing map IDs/details. Then it clicks `+ Add`, captures dropdowns, required fields and DOM event hints, fills dummy values, and never clicks Save/Create/Submit.

Upload only:

```text
runs\<RUN_ID>\UPLOAD_DATAMAP_KB_SUMMARY.zip
```

Important generated files:

```text
runs\<RUN_ID>\datamap_kb\old_datamaps_inventory.json
runs\<RUN_ID>\datamap_kb\old_datamaps_inventory.csv
runs\<RUN_ID>\datamap_kb\old_datamap_id_lookup_by_name.json
runs\<RUN_ID>\datamap_kb\datamap_api_interactions.json
runs\<RUN_ID>\datamap_kb\datamap_form_kb.json
runs\<RUN_ID>\datamap_kb\datamap_dropdowns.json
runs\<RUN_ID>\datamap_kb\datamap_required_fields.json
```


### Data Map API Flow Knowledge Graph

`discover-datamap-kb` also creates a compact Knowledge Graph for the Data Map API-learning phase. It links:

```text
Data Maps page
→ listing APIs triggered by the portal
→ endpoints/request headers/response shapes
→ extracted old Data Map rows and IDs
→ pagination replay calls
→ + Add click
→ Add-form fields, dropdowns and DOM events
→ dummy-fill attempts guarded by the no-save safety policy
```

Important generated files:

```text
runs\<RUN_ID>\datamap_kb\datamap_api_flow_knowledge_graph.json
runs\<RUN_ID>\datamap_kb\datamap_api_flow_knowledge_graph.html
runs\<RUN_ID>\datamap_kb\datamap_api_flow_knowledge_graph.mmd
runs\<RUN_ID>\datamap_kb\datamap_api_flow_knowledge_graph.md
```

These files are included in `UPLOAD_DATAMAP_KB_SUMMARY.zip` and are compact by default. They do not include full raw network bodies.

### Data Map old-map numeric ID enrichment

Use this mode to learn Data Map listing APIs, enrich old Data Maps with numeric `mapId` where read-only detail APIs expose it, then open `+ Add`, capture the form KB/dropdowns/DOM events, dummy-fill, and never save.

```powershell
python -m hip_id_agent.cli discover-datamap-kb `
  --config .\config.yaml `
  --customer DATAMAP-KB `
  --input-json .\examples\uhaul_datamap_dummy_input.json `
  --known-map-id 1670 `
  --crawl-old-datamaps `
  --max-api-pages 250 `
  --max-detail-rows 250
```

Upload only:

```text
runs\<RUN_ID>\UPLOAD_DATAMAP_KB_SUMMARY.zip
```

Important files:

```text
datamap_kb\old_datamaps_inventory_with_ids.json
datamap_kb\old_datamaps_inventory_with_ids.csv
datamap_kb\datamap_id_completion_report.json
datamap_kb\datamap_detail_enrichment_audit.json
datamap_kb\datamap_api_flow_knowledge_graph.html
```


### Data Map blank-page recovery

`discover-datamap-kb` now avoids reloading the Data Maps URL if Dell SSO already landed there. This prevents aborted Angular chunks and blank white pages. If the UI still does not expose rows or `+ Add`, the command retries the page and falls back to the authenticated read-only Data Map summary API:

```text
GET /inaas-gateway/hipService-svc/api/mac-map/summary
```

The fallback output is included in `datamap_api_interactions.json`, `old_datamaps_inventory_with_ids.json`, and the Data Map API Flow Knowledge Graph.


## Data Map old-map ID + form KB discovery

Run:

```powershell
python -m hip_id_agent.cli discover-datamap-kb `
  --config .\config.yaml `
  --customer DATAMAP-KB `
  --input-json .\examples\uhaul_datamap_dummy_input.json `
  --known-map-id 1670 `
  --crawl-old-datamaps `
  --max-api-pages 250 `
  --max-detail-rows 250
```

The flow opens SecureLink Data Maps, learns the list API, extracts old Data Map records, tries bounded read-only detail APIs, then repeats the real UI row action pattern to learn numeric `mapId` values when the list API hides them. It then clicks `+ Add`, captures required fields/dropdowns/DOM events, fills dummy values, and does **not** save/create/submit.

Upload only `runs\<RUN_ID>\UPLOAD_DATAMAP_KB_SUMMARY.zip` for review.

## Rules KB learner

Run after Document Type KB completion:

```powershell
python -m hip_id_agent.cli discover-rules-kb `
  --config .\config.yaml `
  --customer RULES-KB `
  --input-json .\examples\uhaul_rules_dummy_input.json `
  --crawl-old-rules `
  --capture-deep-profiles `
  --max-api-pages 250
```

The Rules learner is read-only. It captures old Rule inventory, numeric `ruleId`, Add form controls/dropdowns, and deep profile details including Rule conditions and Rule actions.

## Dual MCP deterministic execution

The full dummy-fill runner now uses both Microsoft's official Playwright MCP and Chrome DevTools MCP. Before execution it compiles per-phase deterministic plans from the latest learned HIP Portal fast-fill blueprints/form-KB evidence and the current `input.json`. PyAutoGUI MCP is the primary physical click/type/key executor after semantic proof. Playwright MCP supplies accessibility snapshots, deterministic fallback and post-action verification; Chrome DevTools MCP supplies network/console/CDP evidence. Direct Python Playwright is retained only as a logged narrow compatibility fallback for DDS overlay controls.

Validate the environment first:

```powershell
python -m hip_id_agent.cli dual-mcp-check --config .\config.yaml --run-dir .\runs\dual-mcp-check
```

When `--require-mcp` is used, both MCP servers are mandatory. See `PLAYWRIGHT_MCP_RUN_GUIDE.md`.
## Reviewed Unified KB integration

This build imports `knowledge_base/HIP_Unified_Deep_KB.json` and the matching knowledge graph into the persistent Portal Brain before deterministic plan compilation. Use `python -m hip_id_agent.cli import-unified-kb --config .\config.yaml` to seed it manually. See `UNIFIED_KB_RUN_GUIDE.md`.



## Stateful target-branch form execution

HIP forms are treated as dynamic state graphs rather than static empty-form inventories. The current `input.json` defines exact repeatable rows and parent values. After each parent dropdown/radio commit, the runtime recaptures the active form, resolves newly visible child controls semantically, fills them, and verifies exact committed values. Alternative parent branches are explored only after the requested target branch passes, then the target branch is restored and rejudged. See `STATEFUL_KNOWLEDGE_GRAPH_RUNTIME_FIX_20260716.md` and `STATEFUL_KNOWLEDGE_GRAPH_RERUN_GUIDE_20260716.md`.

## Deep Portal Learning

Every full dummy-fill phase now builds a versioned portal model from three independent views of the same authenticated Chrome tab:

1. Python Playwright exact DOM/DDS state.
2. Playwright MCP semantic accessibility snapshot.
3. Chrome DevTools MCP DOM, network, console and optional retry performance trace.

The model learns page fingerprints, controls, validation constraints, parent/child changes, action-to-API relationships, API shapes, navigation edges, console failure signatures, coverage gaps and portal drift. Storage key names may be recorded, but storage values, cookies, tokens and authorization values are not captured.

Learning artifacts are written under `<phase>/portal_learning/attempt_NN/`. Only a phase that passes deterministic verification and the required judges can promote learned facts into Portal Brain. A learning-capture failure is non-blocking and cannot stop form execution.


## Advanced all-phase selection rules

All phase executors now enforce exact-set multi-select transactions, additive selection preservation, explicit safe removal of extras, guarded Select All, exact typeahead option commits, virtualized option stability, radio-group exclusivity, checkbox/switch checked-state verification, dependent-child reset detection, repeatable-row semantic identity and upload completion evidence. Validated fast replay reuses learned bindings but does not skip these safety and exact-state checks. See `ALL_PHASE_ADVANCED_SELECTION_RULES_FIX_20260718.md`.


## Universal HIP form policy and reusable flow memory

Every known HIP Portal form family inherits the same safety and interaction rules, including hidden-parent discovery, explicit radio/dropdown/multi-select events, conditional-child visibility, animation/bounding-box stability, hit-testing, exact-state verification, validation gates and committed-field protection. Unknown pages under `/hybrid-integrations` use the generic safe-form fallback.

The persistent Portal Brain now includes a judge-gated `flow_patterns` memory. It recognizes structurally similar flows from semantic fields, tabs, repeatable rows, parent-child dependencies, interface/transaction traits and validated binding identities. A matching pattern accelerates replay but never supplies customer values; all values still come from the current `input.json`.

## Rules-only until-complete live repair

When Data Map and Document Type are already proven, run only Rules and keep safe self-healing active until the golden Rules state passes:

```powershell
.\RUN_RULES_UNTIL_COMPLETE.ps1 -RunsDir "C:\hip_runs"
```

This mode uses `--rules-only` and `--runtime-self-heal-until-complete`. It alternates exploration and exploitation, captures MCP/DOM/network evidence, compares every failed state with the approved Rules golden screenshot, and still blocks all final portal mutations.

## AgentQ UI/API autonomous mission (2026-07-30)

The recommended end-to-end profile combines the Dell.com crawler's AgentQ actor/critic, deterministic safety gate, exploration/exploitation policy, mutation-settle observation and trajectory memory with the dependency-aware HIP form executor.

Run:

```powershell
.\RUN_AGENTQ_UI_API_AUTONOMOUS_MISSION.ps1 `
  -RunsDir "C:\hip_runs" `
  -ApiMode capture `
  -WriteHeavyEvidence
```

The profile fills Data Map through BizFlow using the current input JSON, captures form-open and fill APIs, builds an input→UI→API crosswalk, exports observed OpenAPI/Postman artifacts and captures the exact final submit payload while blocking all mutation requests before backend delivery.

API write is separately disabled by default. It requires both `-AllowApiMutation` and `$env:HIP_ALLOW_API_MUTATION="YES"`. Exact payload values and authentication headers remain in process memory only and are never stored in Portal Brain or reports.

See `AGENTQ_CRAWLER_UI_API_END_TO_END_FIX_20260730.md` and `LIVE_TESTING_README_AGENTQ_UI_API_20260730.md`.


## Streamlit UI and request/response API capture (2026-08-04)

The end-to-end autonomous mission can now be launched and monitored from Streamlit:

```powershell
python -m pip install -r .\requirements.txt
.\RUN_STREAMLIT_UI.ps1 -Port 8501
```

Open `http://localhost:8501`. The UI can:

- launch and stop the Data Map → BizFlow mission;
- upload a replacement `input.json`;
- monitor phase status and attempts;
- display live console output;
- inspect every observed form API request payload beside its response status/body;
- browse the input → UI control → API-key crosswalk;
- download per-phase payload/response bundles and a redacted evidence ZIP.

Each phase now exports both aggregated contracts and individual request/response transactions:

```text
form_api_intelligence/attempt_<N>/
  form_open_api_transactions.json
  ui_fill_api_transactions.json
  form_api_transactions.json
  api_payload_response_index.json
  form_api_payload_response_bundle.json
```

The CDP observer captures response bodies for all XHR/fetch traffic, mutating methods and HTTP errors even when the portal uses vendor or `text/plain` MIME types. Authorization, cookies, tokens and sensitive fields are masked before persistence.

In `capture` mode, Create/Save/Submit is intercepted and aborted before backend delivery. Therefore the exact mutation request payload is available, but no submit response can exist. Form-open, dropdown, reference, validation and UI-fill responses are captured normally. A real submit response is available only through explicitly confirmed `write` mode.

## BizFlow deep capability learning (2026-08-25)
Run `RUN_LEARN_BIZFLOWS_DEEP.ps1` or `python -m hip_id_agent.cli learn-bizflows-deep ...` to learn Manage Biz Flow listing mechanics, row actions, the complete unsaved multi-tab BizFlow parent/child hierarchy, repeatable Process/filename/routing rows, UI-to-API causality, safe mutation prerequisites, and value-free deterministic replay.

## Full HIP deep learning + capability certification

Run all five deep learners and produce one readiness verdict:

```powershell
.\RUN_LEARN_HIP_FULL_DEEP.ps1 -RunsDir "C:\hip_runs"
```

The mission traverses Data Maps, Document Types, Rules, Transport Profiles and BizFlows using the same persistent Dell SSO browser profile, merges learned capabilities/API contracts/replay profiles, and writes `hip_capability_certification.json` plus a `hip_capability_gap_queue.json`. Missing permission-dependent actions are reported as gaps rather than silently treated as learned.


## Final Governed Change Execution (2026-08-26)

The platform now includes production change governance on top of the Certified Future Task Agent. Use `preview-governed-change` before mutation and `run-governed-change` for execution. Mutation requires an authorized role plus the existing explicit mutation gates, duplicate protection, 2xx write verification, fresh MCP assurance and a hash-chained audit receipt. See `FINAL_FULL_E2E_GOVERNED_PLATFORM_20260826.md` and `LIVE_TESTING_README_FINAL_GOVERNED_20260826.md`.

## Browser-Use WebUI required-feature integration (v1.6.0)

The HIP agent now includes the Browser-Use WebUI capabilities that are useful for governed HIP automation: same-session Browser Use state intelligence, optional own-browser CDP attachment, persistent SSO profile reuse, Pause/Resume human-in-the-loop control, optional video/Playwright trace evidence, explicit download persistence, bounded browser session history, Bun-managed pinned MCP tooling, and input-driven repeatable `+` rows. See `BROWSER_USE_WEBUI_HIP_REQUIRED_FEATURES_20260828.md` for the feature matrix and safety exclusions.

Install/run the JavaScript MCP tooling with Bun:

```powershell
bun install
bun run api
bun run ui
# Optional broader HIP platform UI on port 8502:
bun run platform
```

Browser Use remains a perception/recovery layer. Create/Edit/Delete/Deploy/Migrate remain governed by the HIP certified preview/approval/confirmation/audit path.

### v1.8.2 live execution fixes

- **Full** means a fresh seven-phase run by default: Data Map → Source Document Type → Target Document Type → Rule → Source Transport Profile → Target Transport Profile → BizFlow. Resume is explicit only.
- Transport Profile-only runs no longer fail when long OneDrive run paths prevent copying golden screenshots; the original golden reference is used safely.
- Data Map Map Identifier Version is verification-only, and DDS dropdown state is read from committed selected options/chips so already selected Version/Contivo values are not repeatedly clicked.
### V243 clean-install behavior

`hip-portal` includes its Control Center in the wheel. If it is started before `config.yaml` exists, the UI remains available and `/api/runtime/status` reports `setup_required` instead of failing. Start it from the HIP project directory or set `HIP_PROJECT_ROOT` when the configuration lives elsewhere.

## V243R4 closed-loop learning release (2026-09-22)

V243R4 adds bounded LLM trace self-repair, on-prem multi-model champion/challenger diagnosis, verified deterministic recipe promotion, and Human-in-the-Loop semantic teaching. See `V243R4_TRACE_SELF_REPAIR_HITL_DETERMINISTIC_20260922.md` and `V243R4_FINAL_CERTIFICATION_20260922.md`. The Python package version intentionally remains 2.4.3 for backward compatibility with existing deployment contracts.

### V243R7 completion-first behavior
For production all-phase missions, use the completion-first profile. If a phase cannot yet be proven, the controller keeps the browser open and waits for supervised recovery rather than closing the browser or generating a partial final report. Data Map additionally requires exact proof that the Map Data file was actually committed to the live file input before the phase can pass.

See `V243R7_COMPLETION_FIRST_PHASE_GATE_20260922.md`.
