# V243R14 — Document Type fills the whole form and repairs itself (2026-09-24)

- Fixed Document Type filling only its top section. The child-visibility gate after a commit now waits only for children that commit reveals and whose other structural parents are committed (`eligible_child_nodes`). Name and Data Format Type no longer wait for the identifier Value or attribute Expressions, which appear only after their own Derived From is chosen.
- A field is skipped only when a structural parent failed (`structural_dependencies`). Section and row ordering edges no longer skip unrelated later fields, so the rest of the form is filled and the repair pass or next adaptive cycle repairs only the failed field. Both executors record `ordering_predecessor_failures`.
- Controls are scrolled into view (instantly, overriding smooth scrolling) before the stability and click-target checks. This applies to both executors and to the BrowserSession click/fill preflight (`scroll_control_into_view`, `scroll_locator_into_view`). Below-the-fold controls were rejected as "target center intercepted".
- `BrowserSession.fill_and_log` now reports its real error instead of an `UnboundLocalError`.
- Added a full-length DDS Document Type replica (`tests/fixtures/document_type_full_dds.html`) and `tests/test_v243r14_document_type_full_form.py`, which cover the full form, the BrowserSession broker path, late-dropdown self-repair and preflight scrolling. Added `APPLY_V243R14_IN_PLACE.ps1` / `VERIFY_V243R14_INSTALL.ps1`.

# V243R13 — All-phase completion + learning/RSI unblock (2026-09-24)

- Stale reviews no longer reappear. A new review supersedes older open ones for the same phase (including stopped runs), and committing a phase closes its open reviews. Added `V243R13_FINAL_VERIFICATION_20260924.md`.
- Fixed the endless "Looks correct" loop. Switch evidence records the checked state (`attempt_actual_value`), the judge accepts Enabled/checked equivalence and captures `role=switch`, and **Looks correct** on an exact-completed recovery request commits the phase (`accepted_human_override`).
- Full HIP missions now run recursive self-improvement (replay + model-champion dreaming, skill review) after the replay episode, via the new `mission_learning.close_mission_learning_loop()`. `skill_library_from_config()` is shared by missions and portal tasks.
- Added end-to-end learning-loop tests: continuous learning → replay exploitation, mission RSI → model champions, flow-pattern memory run 1 → run 2.

- Fixed `mask_sensitive_data()` masking `authoritative_*` keys (bare `auth` regex). Strict autonomous gates, exact completion checkpoints and universal task fills received `"***MASKED***"` instead of `True` and could never pass, in any phase.
- Boolean/`None` values are never masked. New `NON_SECRET_KEYS` (`task_tokens`, `session_id`, `sessions`, …) keeps learning memory usable after `safe_write_json`:
  - interactive teaching capture works;
  - recipes, skills and replay policies match after reload.
- `inspect_interaction_state()` returns `validationMessage`. Both executors accept the natural-key "already exists" message as `existing_object_validation`.
- Disabled/read-only portal-owned controls verify against their displayed value (value or placeholder). Mismatches report `HIP_READONLY_PORTAL_VALUE_MISMATCH`.
- The Document Type scanner, comparator and executor support the DDS Status switch.
- The validation gate accepts the golden duplicate messages for all 7 phases. Row-level duplicates and conflicting objects still block.
- Autonomous failures carry `unmet_success_checks`, `last_cycle_execution` and `failure_summary`. `autonomous_target_execution()` is used by all phase KB modules.
- `websockets` pin moved from 13.1 to 15.0.1 (required by `browser-use==0.13.8`).
- Added Chromium replica tests for Data Map, Document Type, Rule, Transport Profile, BizFlow and a task-box form fill, plus learning-memory round-trip tests.

# V243R12H1 — strict learning promotion + browser-action model reward (2026-09-23)

- Human approval is now tri-state: missing review is UNKNOWN, never implicit PASS.
- `promote_only_after_exact_judge_human_pass` is enforced at runtime.
- `learn_from_fill`, `learn_from_click`, `learn_from_navigation`, `learn_from_search`, and failed-action learning switches are enforced at runtime.
- Failed/semantically-unverified actions remain negative/observed evidence and are excluded from trusted `followed_by` routes, Portal Brain validated transitions, and deterministic replay steps.
- AutoWebGLM `action_selection` model tournaments are now rewarded from the exact physical browser action outcome (click/fill/search/press), including negative drift/failure reward.
- Added four hardening regressions; verified total is 1,276 collected = 1,275 passed + 1 expected skip.

## V243R12 — 2026-09-23

- Added continuous portal learning from real browser fill/click/navigation events.
- Exact + judge + human-approved interaction sequences update Capability Graph, Portal Brain transition memory, trajectory memory and replay policy.
- Failed actions are retained as negative/candidate evidence; customer values/selectors/coordinates are not persisted.
- Fixed hidden AutoWebGLM single-model path: live action proposals use the Dell On-Prem model portfolio during learning/complex tasks.
- Learning mode cannot collapse to a single champion; configured minimum distinct models and parallel challenger counts are enforced when available.
- Added Dell AIA text-deployment availability probing with TTL cache and a persistent `model_availability.json` ledger.
- Added `model_usage.jsonl` so Control Center/runtime evidence shows the candidate models actually used per role/tournament.
- Added role-aware availability/usage visibility for planning, action selection, judge and recovery.
- Added Production Doctor multi-model-learning readiness check.
- Control Center model-portfolio refresh now probes reachable Dell deployments and displays configured/available/recently-used model counts.
- Full repository suite: 1,271 passed, 1 skipped, 0 failed (1,272 collected).
- Package compatibility remains 2.4.3.

## V243R11 — Interactive Teaching + Immediate Deterministic Promotion

- Human `Looks correct` + exact reproof + judge PASS atomically promotes the phase to deterministic semantic replay.
- Added live interactive teaching: Start teaching → navigate/click in HIP → Finish & learn → exact reproof → supervised promotion.
- Accepted phases cannot be reopened by non-authoritative learning/API/observability enrichment.
- Demonstrations persist semantic routes/actions only; no customer values, raw selectors, or coordinates.

# V243R10 — 2026-09-23

- Added terminal read-only live phase reproof before `HIP_PHASE_NO_PROGRESS_WATCHDOG` blocks a stable form.
- Watchdog checkpoint providers may now be async so completion can be proved from the current browser.
- Human `Looks correct` on stale/missing execution artifacts triggers live read-only reproof rather than another fill cycle.
- Added attempt-scoped invalidation for `phase_live_read_only_reproof.json` to prevent stale proof reuse.
- Added `phase_learning_memory_receipt.json` after successful judged phases to expose persistent structural learning.
- Persistent customer-independent learning remains under `reporting.memory_dir`; customer values are not promoted into reusable memory.

## V243R6 / 2.4.3 - Judge Reconciliation + One-Time Learning HITL

- Reconcile exact browser completion evidence with deterministic, text and vision section judges.
- Add bounded on-prem multi-model judge consensus for model-only disagreement/uncertainty.
- Prevent multi-model or human approval from overriding failed exact evidence.
- Add one Human Review checkpoint per newly learned phase for both automated PASS and BLOCKED outcomes.
- Add Control Center `Looks correct` / `Needs correction` workflow and phase-review APIs.
- Feed resolved supervised review back into judge champion/challenger outcome learning.
- Preserve no-destructive-replay behavior when exact completion is proven and only model judges disagree.
- Add boolean-safe consensus evidence handling so security masking cannot change verdict truthiness.
- Focused regression: 110 passed, 0 failed. Full suite: 1,244 passed, 1 skipped, 0 failed (1,245 collected).
- Package compatibility version remains 2.4.3.

## V243 R2 / 2.4.3 - Production Hardening Final

- Re-check immutable execution fingerprints immediately before browser dispatch.
- Block mutations when the governance hash chain is malformed or tampered.
- Add production lease heartbeat for long browser missions.
- Honor `single_active_browser_session: false` while keeping `true` as the safe default.
- Replace broad Markdown inclusion with a strict safe-review allowlist and per-file SHA-256 manifest.
- Persist sanitized exception diagnostics without locals.
- Add production hardening regressions; full suite: 1,233 passed, 1 skipped, 0 failed (1,234 collected).
- Package version remains 2.4.3 for compatibility with existing V243 version assertions and deployment pins.

## V243 / 2.4.3 - Production End-to-End Runtime Closure

- Added one-command production lifecycle: doctor/preflight -> governance -> single-browser lease -> plan -> execute -> exact verify -> journal -> safe review bundle.
- Added cross-process browser execution lease with stale-process reclamation and collision-resistant run ids.
- Added production request manifests with SHA-256 fingerprints of config/input/golden references without persisting business values.
- Added role/approval/three-key mutation gate integration plus duplicate and unresolved prior-mutation quarantine.
- Added local pre/post mutation evidence capture and explicit compensating-action/rollback guidance; unsafe automatic rollback remains disabled.
- Added tamper-evident SHA-256 hash-chained production journal and journal verification command/API.
- Added production doctor checks for Python, AutoGen 0.7.5, input contract when required, storage, browser lock, MLflow, On-Prem model portfolio, replay cache and live runtime certificate for mutations.
- Added same-process FastAPI Control Center static serving; Bun is optional for normal production operation.
- Added `hip-agent` and `hip-portal` console entry points and production endpoints/UI controls.
- Added safe review bundle generation that excludes screenshots/raw portal evidence by default.
- Added `/api/runtime/status.production_e2e` observability for production lifecycle settings.
- Package version: 2.4.3.

## V242 / 2.4.2 - Downstream-Proven Model Promotion + Release Closure

- Durable model champions are no longer persisted from proposal-only quality; promotion requires real downstream portal outcome evidence.
- Added task-specific downstream model statistics and task champions, blended with generic role evidence.
- Enforced declared role capability when selecting text models for planning, action selection, judging and recovery.
- Exploration no longer prioritizes historical champions ahead of least-tried eligible challengers.
- V241 model-state migration preserves evidence counters but discards proposal-only champion labels.
- Recursive self-improvement now honors replay-policy/model-portfolio/skill-update configuration switches without double-counting skill outcomes.
- Removed hardcoded universal-task fallback version and closed the standalone wheel import smoke gap.
- Package version: 2.4.2.

## V241 / 2.4.1 - Recursive Self-Improvement + On-Prem Multi-Model Portfolio

- Added On-Prem-only Dell AIA model catalog and rejected cloud/GCP/Anthropic candidates.
- Added champion/challenger model routing for planning/action selection with bounded parallel proposals.
- Added downstream reward learning from actual task completion, exact input coverage/readback, repeatable-row correctness, mutation verification, recovery cost and latency.
- Added per-role model rankings and fast single-model exploitation once a champion is sufficiently proven.
- Added bounded recursive self-improvement cycles over replay policy + model portfolio; runtime source-code rewriting remains disabled.
- Added Control Center model champion and recursive-improvement status plus API endpoints.
- Expanded multimodal candidate set with On-Prem `florence-2-large-ft`; `nomic-embed-vision-v1-5` remains an embedding specialist, not a chat model.
- Retained V240 replay/dreaming, V239 skill induction, V238 async MLflow, V237 runtime-input/golden guidance, and all mutation/browser safety controls.
- Package version: 2.4.1.

## V240 / 2.4.0 - Replay-based Exploration / Exploitation Policy Improvement

- Added disk-backed replay episode memory for successful and failed portal runs.
- Added goal scoring using mission success, exact runtime-input coverage, repeatable-row completion, mutation verification, efficiency and recovery cost.
- Added exploration, hybrid and exploitation policy modes.
- Added offline dreaming-based policy improvement over historical replay episodes; baseline policy is included among candidates and the best replay value is cached for the next run.
- Added old-run ingestion into the replay cache without persisting customer values, selectors, XPath or coordinates.
- Added high-level replay workflow fast paths plus live semantic re-proof and adaptive fallback.
- Added AgentQ action-level replay-policy hints so safe unseen actions receive exploration weight and historically successful actions receive exploitation weight.
- Added Control Center Replay Policy metrics/table/history and `/api/replay-policy`.
- Added async MLflow policy-selection, episode-score and dreaming telemetry.
- Retained all V239 skill induction, V238 MLflow, V237 runtime-input/golden, and previous execution safety controls.
- Package version: 2.4.0.

## V239 / 2.3.9 - Universal Portal Learning + Skill Induction Engine

- Added a governed Universal Portal Operator for user-requested activities beyond the original seven HIP phases.
- Added value-free Skill Induction Engine that promotes exact-verified successful trajectories into reusable semantic skills.
- Validated skills now instantiate the actual workflow for compatible future tasks using the current URL, entity and input.json; every step is re-proven live.
- Added bounded adaptive execution for unfamiliar visible actions explicitly named by the user.
- Added non-mutating skill-replay drift fallback to adaptive live discovery; mutation failures are never auto-retried.
- Added freshness-aware skill confidence and drift-suspect demotion.
- Added Control Center induced-skill library and `/api/skills`.
- Retained V238 asynchronous MLflow observability and all V237/V236/V235/V234 execution protections.
- Package version: 2.3.9.

## 2.3.7 — 2026-09-17 — Runtime input-ledger + golden-guided fill hardening

- Runtime input.json is now enumerated leaf-by-leaf on every adaptive cycle; nonblank fields that are absent from the static compiler can no longer be silently omitted.
- Newly observed input-owned controls are converted into transient semantic execution nodes using live labels/framework keys/section/row identity, never persisted selectors or coordinates.
- Dependent controls revealed after parent actions force another execution cycle before the phase can pass.
- Golden reference screenshots are attached to the shared browser session and used proactively as advisory visual structure evidence for Dell AIA/AutoGen binding repair.
- input.json remains the sole authority for customer values; golden screenshots can guide structure but cannot invent or mutate values.
- BizFlow runtime input accounting is tab-aware across Flow Details, Configure Source, Configure Target/Process Steps, and Configure Routing.

## 2.3.6 — 2026-09-17 — All-phase field completeness and completion-gate correction

- Every nonblank input-owned graph node is now completion-blocking until exact readback succeeds, even when the portal marks the control optional.
- Semantic binding repair now targets every supplied unresolved attribute, not only HTML/DDS required controls.
- Strict execution audit reports 100% input-owned node coverage and unresolved node IDs.
- Deterministic replay readiness is now a learning/reuse warning only; it no longer converts an exactly filled current form into BLOCKED.
- Existing V235 browser-session ownership and semantic-anchor recovery remain intact.

## V233 Stage 4 — Operational World Model

## 2.3.5 — 2026-09-17 — Live browser-session ownership and semantic-anchor hotfix

- Keep `data-hip-semantic-anchor` in semantic inventory even for custom/DDS container controls.
- Prefer the exact vetted Playwright anchor over unanchored semantic lookalikes.
- Add an exact-anchor structural action policy for navigation/open/expand/tab/add-row operations; destructive/final mutations remain on the stricter execution policy.
- Prevent the Browser-Use observer from calling public browser lifecycle shutdown on the HIP-managed browser/CDP session.
- Add Playwright-based value-free Browser-Use-style observation when HIP owns the browser.
- Classify and recover the observed `no browser is open`, `browser not connected`, CDP reconnection failure, `ConnectionRefusedError`, and Windows 1225 disconnect signatures.
- Add regression coverage from the 2026-09-17 live failure bundle.

- Added `AutonomousPortalTransitionPlanner` for selector-free Create/Edit/Save/Validate/Clone/Migrate/Deploy/Add Row/Next/Back progression.
- Added fail-closed dynamic dropdown/Process Step option resolution from current mounted options, mission context, and validated world-model branches.
- Added certified semantic-action execution without forcing live actions through unresolved legacy capability ids.
- Preserved mutation authorization and no-duplicate-dispatch reconciliation.
- Retained Stage-3 semantic memory safety and SFTP-HAFT role-aware deployment-group defaults.

# V233 Stage 3 — 2026-09-11 — Persistent website world model

- Added `WebsiteWorldModelMemory` with candidate/validated/negative trust for semantic controls, states and transitions.
- Promotes knowledge only after deterministic post-action effect verification; failures/no-effect outcomes create negative evidence.
- Learns parent/child dependency diffs by recording controls revealed/hidden by verified transitions.
- Learns portal-owned dropdown/process-step choices only when the chosen label was proven to exist in the live mounted option list; arbitrary typed/customer values are rejected from memory.
- Feeds bounded world-model hints into semantic target ranking, AutoWebGLM context, Agent Live View and allow-listed runtime self-heal context; live evidence remains authoritative and every remembered target requires current re-proof.
- Persists no CSS/XPath selectors, generated ids, screen/viewport coordinates, bounding boxes or customer field values.
- Added backend world-model summary/recommendation endpoint and Control Center persistent-memory visibility.
- Added role-aware SFTP-HAFT deployment-group policy: Sender/Dell/source `da-sender-sftphaft-dce-shared`; Partner/Receiver/target `pt-receiver-sftphaft-dce-shared`. Explicit non-legacy overrides and non-SFTP interfaces remain untouched.
- Updated UHAUL example input contracts and the unified KB canonical SFTP-HAFT correction.
- Validation: 1,133 passed + 1 environment-only Chromium skip; 0 failures.

# v2.3.2 — 2026-09-10

- Preserved the shared autonomous/adaptive goal runtime as the final completion authority for all seven HIP phases.
- Changed strict live completion to fail closed when exact or authoritative execution-stage proof is missing; an inner `pass=True` alone can no longer complete a phase.
- Sanitized adaptive replay/learning artifacts so transient CSS/XPath selectors, bounding boxes and screen/viewport coordinates are not durable knowledge.
- Changed normal `config.yaml` / `config.example.yaml` to adaptive hybrid witness mode; strict all-MCP enforcement remains in `config.mcp-required.windows.yaml` and explicit `--require-mcp`.
- Changed Control Center and independent `run-section` execution to use `--allow-executor-fallback` by default.
- Removed the obsolete `--allow-playwright-fallback` section-run flag.
- Added hybrid GO/NO-GO executor quorum so a healthy governed PyAutoGUI/Playwright executor can continue when optional MCP witnesses are unavailable.
- Quarantined Playwright MCP from form dispatch until it proves the same authenticated HIP tab as the Python Playwright session; optional witness drift degrades instead of stranding adaptive execution.
- Kept strict-MCP certification available for environments that explicitly require Playwright MCP + Chrome DevTools MCP + HIP Intelligence MCP.
- Validation: 1,115/1,115 repository tests passed in four non-overlapping batches; Python/backend/frontend compilation passed; all shipped YAML configs parsed; JavaScript syntax checks passed with Node; v2.3.2 wheel built and smoke-imported.

# v2.3.1 — 2026-09-10

- Promoted `autonomous_form_runtime` from Data Map-only completion to all seven form phases.
- Source/Target Document Type now run the dedicated one-to-one DDS transaction executor inside bounded autonomous observe/rebind/retry cycles.
- Rule and Source/Target Transport Profile keep phase-specific row/dependency helpers as accelerators, while the shared autonomous runtime owns final target-state proof and post-exploration restore.
- BizFlow runs autonomous goal reconciliation per visible tab/section before section judging and progression.
- Added section-scoped autonomous execution so a BizFlow tab only plans/binds its current graph section.
- Added controlled per-family rollback flags plus `apply_to_all_form_phases=true` as the default authoritative policy.
- Autonomous mission mode explicitly forces all seven phase-family flags on.
- Added explicit Document Type execution-stage audit for exact and authoritative transaction proof.
- Mission Trace/Web UI now report `autonomous_all_form_phases` and the all-phase adaptive loop.
- Added V231 regression tests for seven-phase enablement, module wiring, section scoping, and dedicated-executor/file-upload ownership.
- Validation: 1,107/1,107 repository tests passed; 98 Python modules compiled; all three shipped YAML configs parsed with all-phase autonomy enabled; wheel built; extracted-package smoke verification passed.

# v2.3.0 — 2026-09-10

- Added goal-driven `autonomous_form_runtime` for Data Map.
- Removed the fixed Data Map field-fill order from the live execution path.
- Each adaptive cycle re-captures the active live form and treats current DOM/accessibility evidence as authoritative.
- AutoWebGLM observation is captured each cycle; BrowserSession still gates every physical action through AutoWebGLM semantic intent.
- Dell AIA/AutoGen form planning is advisory only on ambiguous bindings and can only map to controls that exist in the current live DOM.
- PyAutoGUI MCP remains preferred; Playwright MCP and Python Playwright remain immediate verified fallbacks.
- Required file controls are discovered after dependency fields are executed, then uploaded through the governed contract-aware uploader.
- Full goal state is re-verified after every adaptive cycle with exact and authoritative execution evidence.
- No selectors, Angular ids, DOM indexes or screen coordinates are persisted as durable learning.
- Added V230 regression tests for changed-label adaptation, dynamic goal execution and no fixed manual Data Map fill order.

# v2.2.9 — 2026-09-10

- Routes all governed DDS form interactions through BrowserSession instead of allowing the form driver to bypass the PyAutoGUI/Playwright execution ladder.
- Canonicalizes already-proven Locators into unique same-generation CSS selectors before Playwright MCP fallback, preventing human action descriptions from being interpreted as selectors.
- Keeps AutoWebGLM as the primary planner/intent-alignment layer and records the actual executor for every action.
- Makes PyAutoGUI MCP preferred-but-optional in the normal hybrid profile; Playwright MCP is an authoritative fallback on the same authenticated browser.
- Adds exact authoritative completion gating: visible values alone cannot complete a live phase.
- Corrects Data Map Status handling for DDS switch/checkbox controls and adds supervised value-free Create Map surface signatures from the supplied portal screenshots.
- Corrects Mission Trace so blocked-phase diagnostic continuation is not reported as a verified handoff.
- Validation: 1,096/1,096 tests passed in four non-overlapping batches; 29 targeted hybrid/in-page/runtime tests passed; Python compilation and three shipped config parses passed.

# v2.2.8 — 2026-09-10

- Promotes PyAutoGUI MCP to primary visible-desktop click/type/key executor after semantic target proof.
- Makes Playwright MCP the deterministic browser fallback and post-action verifier.
- Adds structural `+ Add` visual recovery using Browser-Use/Gemma perception plus PyAutoGUI coordinate clicking.
- Preserves same-page/in-page create flows for Data Maps, Document Types, Rules, Transport Profiles and BizFlow.
- Adds optional isolated browser-use/web-ui sidecar tooling and a real-Chromium local HIP mock regression.
- Validation: 1,090/1,090 tests passed in non-overlapping batches.

# v2.2.7 — Data Maps In-Page Create Form Route Lock

- Data Maps `+ Add` is now treated as an in-page structural opener; a navigation to a different route is rejected as a wrong Add target.
- Removed the duplicate Data Map pre-click that could dispatch before the ReAct form-entry controller owned target selection.
- Data Map Add discovery ranks exact page-level/top-right `+ Add` controls and rejects row/menu/form-local Add controls and route-changing anchors.
- Create Map form-entry proof now accepts the progressive initial drawer (`Create Map` + core controls) instead of requiring later/lazy upload-validation controls.
- Same-path query/hash changes are allowed for drawer state; path changes are not.
- Wrong-route Add attempts are recorded and recovered through bounded ReAct canonical-listing restoration.

## 2.2.6 - 2026-09-09

- Auto-renew missing/expired/mismatched Windows live-runtime certificates during Live GO/NO-GO when prerequisite static/browser/model/path checks are healthy and no mission is active.
- Bound runtime certificates to the package version and made the configured `latest_certificate_relative_path` authoritative instead of using a fixed path.
- Made shipped `config.yaml` require PyAutoGUI MCP and keep the non-mutating certification proof mandatory for Live GO/NO-GO.
- Promoted PyAutoGUI MCP ahead of local in-process Playwright fallback after Playwright MCP failure for semantically proven non-mutating/structural web clicks.
- Added PyAutoGUI MCP-first exact-verified recovery for business-field fills and key actions; search fields remain Playwright/local-first and final tenant mutations remain Playwright-governed.
- Added Live Readiness UI messaging for automatic certificate renewal and Dell SSO completion when Chrome opens during renewal.
- Added v2.2.6 regression coverage for certificate auto-refresh, certificate-path configuration, package-version fingerprinting, and PyAutoGUI-first click/fill/key recovery order.

## 2.2.5 - 2026-09-09

- Made `execute_phase_state_graph()` authoritative for Data Map, Rules, Transport Profile and BizFlow even when legacy module-specific control inventories are empty.
- Added Playwright/shadow-DOM-aware `capture_stateful_controls()` fallback/merge and a page-level Playwright fallback when the active form root yields zero controls.
- Added strict live execution stages: form opened, controls discovered, controls bound, fields filled/exactly verified, exact execution verified.
- Added fail-closed errors `HIP_FORM_CONTROLS_NOT_DISCOVERED`, `HIP_FORM_CONTROLS_NOT_BOUND`, `HIP_PHASE_EXACT_EXECUTION_NOT_COMPLETED`, and `HIP_PHASE_EXACT_EXECUTION_NOT_VERIFIED`; all route through bounded ReAct active-surface recovery.
- Prevented independent section judging until the exact deterministic browser execution checkpoint passes.
- Corrected exact-completion checkpoint logic to respect the state executor's authoritative `failed_attempts` list instead of treating optional/conditional informational attempts as blocking failures.
- Aggregated strict execution-stage proof across BizFlow tabs and surfaced the earliest failed execution stage in Mission Step Trace.
- Added v2.2.5 regression coverage for empty-control, wrong-listing-control, legacy-gate removal, strict BizFlow execution, pre-judge gating, checkpoint semantics and self-heal classification.

## 2.2.4 - 2026-09-09

- Added `ensure_phase_form_entry()` as the mandatory ReAct-controlled form-entry transaction for Data Map, Document Type, Rule, Transport Profile and BizFlow.
- Standard phase flow is now listing -> semantic page-level `+ Add` -> governed click -> exact form-surface proof before any field fill.
- BizFlow flow is now listing -> `+ Add` -> template/card picker -> direct template link/action (with governed overflow recovery) -> multi-tab form proof.
- Added bounded BizFlow tab ReAct verification so Source/Target/Routing controls are never filled until the requested tab is proven active.
- Form-entry and tab-entry failures raise `HIP_FORM_ENTRY_NOT_OPENED` / `HIP_BIZFLOW_TAB_NOT_OPENED`, classified for autonomous active-surface self-heal instead of silently continuing on the wrong surface.
- Page-level `+ Add` actions are explicitly governed structural openers.
- Added v2.2.4 regression coverage for standard phase entry, autonomous route-back retry, BizFlow intermediate/template flow, tab re-observation and fail-closed behavior.

## 2.2.3 - 2026-09-09

- Made Google Chrome Stable the default persistent Dell SSO/HIP browser; Edge and Playwright Chromium remain startup-only fallbacks.
- Forced UTF-8 environment/stdio across backend, Bun, Streamlit, PowerShell launchers, and mission child processes to eliminate Windows cp1252 `UnicodeEncodeError` crashes.
- Replaced generic flattened phase expectations with canonical semantic state-graph expectations for all governed HIP phases.
- Included every non-empty live form control in post-fill judge evidence, including normal enabled/writable/non-required fields.
- Corrected phase-handoff trace ownership so destination navigation/verification appears under the destination phase while source cleanup stays under the source phase.
- Added concise deterministic missing-field plus text/vision judge diagnostics to blocked phase cards.
- Added v2.2.3 regression coverage for Chrome failover/CDP health, UTF-8 safety, exact field/row judge matching, normal-control evidence retention, trace attribution, and release configuration.

## 2.2.2 - 2026-09-08

- Removed client-side output-token limits from Dell AIA text and Gemma vision requests by default.
- Added optional positive token-limit overrides without imposing a default cap.
- Prefer final assistant output over reasoning/analysis to keep gpt-oss JSON/text responses clean.
- Unified robust response extraction across vision preflight, runtime vision, section judge, and optional vision verification.
- Added strict JSON extraction and response previews for text/vision model availability UI.

## 2.2.1 - 2026-09-08

- Increased Dell AIA text availability probe budget from 32 to configurable `AIA_TEXT_PROBE_MAX_TOKENS` (default 256, minimum 128) for reasoning-heavy `gpt-oss-120b` deployments.
- Expanded Dell AIA assistant-text extraction to choice-level and message-level `reasoning`, `analysis`, `answer`, `completion`, `generated_text`, `output`, and related aliases.
- Added response-shape diagnostics (`choice_keys`, `message_keys`, `finish_reason`) when Dell returns HTTP success without extractable assistant text.
- Added regression coverage for the live screenshot failure where `choices` existed but assistant text was not extracted.

# v2.2.0 — Dell AIA / Gemma Vision / MCP Runtime Reliability (2026-09-08)

- Deterministic project `.env` loading plus optional `HIP_ENV_FILE` override.
- Fixed Dell AIA text-model false-negative availability probes.
- Added robust Dell AIA response-envelope extraction.
- Fixed `VISION_MODEL_NAME` handling and internal vision-selection contamination.
- Replaced the fragile 2-pixel vision probe with a 64×32 red/blue capability image.
- Added `max_tokens`/`max_completion_tokens` vision request compatibility.
- Raised bounded MCP stdio stream capacity for screenshot JSON-RPC payloads.
- Live runtime certification now uses a small PyAutoGUI screenshot region and isolates desktop-probe failures.
- Added regression coverage for the exact Windows errors observed by the operator.

# v2.1.9 — Live Windows Runtime Certification Gate (2026-09-06)

- Added `hip_id_agent.live_runtime_certification` to close the final source-vs-live gap.
- Added `python -m hip_id_agent.cli certify-live-runtime` for a non-mutating certification on the actual Windows HIP workstation.
- Certification launches the headed persistent Edge/Chrome profile, waits for Dell SSO when required, proves same-browser Playwright MCP and Chrome DevTools MCP, verifies HIP Intelligence MCP semantic tools, probes Dell AIA text + vision, and performs a read-only PyAutoGUI MCP desktop smoke (`size`, `position`, `screenshot`).
- No PyAutoGUI click/key/write is issued during certification and HIP mutation authorization remains disabled.
- Added a hash-integrity-protected, runtime-fingerprinted, expiring `live_runtime_certificate.json`.
- Added verification of the latest certificate to normal Live GO/NO-GO. The shipped `config.yaml` requires a recent passing certificate before issuing a mission readiness receipt.
- Added `/api/mission/live-runtime-certification` and a **Certify Windows runtime** control to the JavaScript UI.
- Added 20 v2.1.9 regression tests for certificate integrity/freshness/fingerprint enforcement, no-mutation guarantees, API/CLI/UI integration, and readiness blocking.

# v2.1.8 — PyAutoGUI MCP Governed Desktop Fallback

- Adds optional Windows-only `pyautogui-mcp==2026.1.101837` as a tertiary desktop/native interaction channel.
- Playwright MCP remains the sole governed web-control executor; Chrome DevTools MCP remains the independent witness and HIP Intelligence MCP remains the semantic brain.
- Adds MCP capability discovery/audit for size, position, click, write, press, hotkey and screenshot operations.
- Upgrades the existing PyAutoGUI fallback to prefer MCP and records the actual desktop executor in action provenance.
- Adds high-confidence native/browser-chrome coordinate recovery with explicit evidence provenance and a default 0.97 threshold.
- Keeps final tenant mutations disabled through both PyAutoGUI MCP and local PyAutoGUI by default.
- Live readiness treats PyAutoGUI MCP as optional unless `mcp_required=true`.
- Adds 25 v2.1.8 regression tests covering transport configuration, tool contracts, executor preference, mutation blocking, native evidence gating, readiness semantics, runtime attach/close, and package policy.

# v2.1.7 — HIP Semantic Control MCP / Website Understanding

- Implements the complete website-specific HIP semantic intelligence layer inside the existing HIP Intelligence MCP rather than adding another generic browser MCP.
- Adds all requested `hip_*` tools for current surface, form schema, semantic control lookup, owned popup, repeatable rows, required/current fields, expected-vs-actual comparison, safe actions, action-effect verification, route identity, and DOM/form generation.
- Adds browser-find-first ephemeral ref resolution with label/role/section checks and fail-closed ambiguity handling.
- Adds structured Playwright MCP `browser_fill_form` execution for groups of independently verified ordinary text fields; each field is semantically preflighted, AutoWebGLM-aligned, ref-resolved, exact-commit verified, and post-action effect verified. No raw fallback is permitted after a governed batch failure.
- Makes HIP Intelligence MCP required by default in every shipped config and pins official `@playwright/mcp` to 0.0.79 in config, package metadata and launch scripts.
- Live GO/NO-GO now requires `browser_find`, `browser_fill_form`, and the complete HIP semantic tool inventory.
- Adds explicit 0.90 execute / 0.75 re-observe / 0.55 rediscover-self-heal thresholds and fused per-source evidence telemetry.
- Adds SAFE / CONDITIONAL / DANGEROUS semantic action classification feeding the existing mutation authorization layer.
- Expands MutationObserver evidence into dialog/drawer/listbox/row/spinner lifecycle, accordion and field-state changes, and route-change events.
- Keeps Chrome DevTools MCP as an independent witness and Gemma vision as ambiguity-only confirmation.
- Adds 25 v2.1.7 architecture regression tests. Full source regression: **915/915 PASS**.

# v2.1.6 — Pasted Requirements Verified / Strict Discovery MCP Execution

- Re-audits the exact `Pasted markdown(6).md` requirements against executable source rather than prior certification prose.
- Closes the remaining read-only dropdown discovery gap: when Layer-11 semantic runtime is active, a failed/unavailable Playwright MCP click now fails closed and can no longer fall through to raw `locator.click()`.
- Preserves standalone/offline legacy behavior only when the governed semantic runtime is not attached.
- Adds an explicit 25/25 pasted-requirements traceability regression suite covering intended-control MCP evidence, legacy setter fail-closed guards, TP DDS guard ordering, Live Witness DOM telemetry, blocked mutation violations, mutation authorization provenance, generation-scoped repeatable rows, volatile sticky selectors, strict MCP inventories, `press_key`, Add recovery, and governed BizFlow structural actions.
- Full source regression: 890/890 PASS (865 prior tests + 25 new traceability tests).

# v2.1.5 — Final Layer-11 Convergence Hardening

- Requires intended-control evidence from Playwright MCP, Chrome DevTools MCP, and HIP Intelligence MCP under the fail-closed semantic runtime.
- Prevents semantic failure from falling through to legacy raw Playwright/JavaScript setters or Add-form click recovery.
- Adds generation-scoped repeatable-row binding and rejects positional sticky selectors after Angular rerender.
- Adds independent browser DOM-click witness telemetry, mutation authorization synchronization, and structural-opener provenance.
- Routes read-only dropdown discovery and BizFlow structural actions through semantic proof → AutoWebGLM → Playwright MCP → post-action verification.
- Adds keyboard `press_key` intent support and strict live GO/NO-GO MCP tool-inventory validation.
- Adds 16 final-convergence regression tests (865 total collected tests).

# v2.1.4 — Semantic Website Understanding + Multi-Evidence Action Gate

- Adds value-free semantic control fingerprints and stable `SC-...` identities across HIP Angular/DDS rerenders.
- Fuses Playwright MCP accessibility, Chrome DevTools DOM, HIP Intelligence MCP consensus, learned fingerprint memory and ambiguity-only Gemma vision before operational execution.
- Adds fail-closed confidence and runner-up ambiguity gates plus just-in-time semantic fingerprint revalidation.
- Extends semantic proof/effect verification through normal click/fill/search, DDS single/multi-select, checkbox/radio/toggle, tabs, repeatable-row Add, structural-parent exploration and file uploads.
- Expands HIP Intelligence MCP with semantic resolve/rank/fingerprint/effect/capability tools and makes those tools part of Live GO/NO-GO.
- Mission Trace/UI expose semantic action provenance and exact post-action effect evidence.
- Full source regression at promotion: 849/849 PASS.

# v2.1.3 — Non-Mutating Live Witness / UAT Gate

- Adds a dedicated live witness profile for the first Dell HIP tenant test.
- Exercises the real single Dell SSO browser, AutoWebGLM primary planning, official Playwright MCP primary execution, Chrome DevTools MCP verification, Dell AIA text/vision judges, and every selected P01-P07 form.
- Witness mode is fail-closed non-mutating: API write/mutation authorization are rejected, submit-request capture is disabled after all other profiles are applied, and Create/Save/Submit/Finish/Deploy/Delete-style controls are prohibited.
- Dynamic `+ Add` form/row openers remain allowed so real DDS structure, repeatable rows, dropdowns, fills, route handoffs and judges are exercised without saving.
- Adds `live_witness_certificate.json`, which independently checks browser action evidence and observed network traffic before the final mission verdict. A mutation-control click or mutation-classified request prevents witness success.
- Live-readiness receipts are now bound to the execution profile, so a standard receipt cannot start a witness run (or vice versa).
- JavaScript Control Center defaults the first live test to witness mode, forces API capture mode, disables API mutation authorization, and clearly labels the witness guarantee.
- Full source regression at promotion: 839/839 PASS.

# v2.1.2 — Live GO/NO-GO Readiness Gate

- Adds a fail-closed live mission-readiness gate before any portal mission launch.
- Verifies browser startup, official Playwright MCP execution tools, Chrome DevTools MCP judge tools, Dell AIA text and vision probes, safe writable runs path, AutoGen/AutoWebGLM policy, expert-skill/input contract, and mission-idle state.
- Adds a 10-minute server-side readiness receipt bound to the exact mission fingerprint; config/input/scope/API-mode changes invalidate it.
- Backend mission start now requires the matching unexpired receipt and returns HTTP 412 when the live gate is missing or stale.
- JavaScript UI exposes `Run Live GO/NO-GO`, displays per-check PASS/WARN/BLOCK status, and keeps Start disabled until GO.
- Static mission preflight now receives the UI-selected config and runs path.
- Full source regression at promotion: 830/830 PASS.

# v2.1.1 — All-Phase Transition Coordinator

- Routes a completed/blocked phase directly to the next selected phase that still requires live execution; resumed complete phases are skipped without browser navigation.
- Adds a crash-safe mission transition ledger with pending destination acknowledgement. A destination clears the pending handoff only after its own route and dual-MCP preflight pass.
- Materializes resumed exact-state/judge/verification/assurance proof into the current run, removing final-certification dependence on the prior run directory.
- Prevents a strict assured mission from inheriting a phase that lacks assured source evidence.
- Adds a terminal completion gate: all selected phases must be complete with current-run proof and no transition may remain unacknowledged before `application_complete` can be true.
- Full source regression at promotion: 822/822 PASS.

# v2.1.0 — Completion-First Mission Hardening

- Fixed Windows long-path directory creation and compacted generated run/evidence paths.
- Removed exhaustive Data Maps behavior from the normal business mission; no-vetted-intent AutoWebGLM recovery is observation-only.
- Added Edge -> Chrome -> Chromium pre-SSO startup fallback while locking the selected browser after authentication.
- Made AutoWebGLM + official Playwright MCP the explicit planner/executor contract across DDS and ordinary controls.
- Added real text-model and fresh multimodal vision-model availability probes.
- Added P01-P07 live mission trace with observed/filled/clicked/verified evidence and executor provenance.
- Added same-browser MCP reconnect/rebind after CDP/WebSocket transport loss.
- Added active structural no-progress watchdog and exact-state post-completion stall handling.
- Added deterministic, dual-MCP-proven phase handoff so P01-DM completion actively transitions to P02-SDT instead of remaining on Data Maps.
- Full source regression at promotion: 813/813 PASS.

# v2.0.0 — Cross-Layer Convergence Hardening

- Adds a physical-dispatch causality fence: preflight/background writes that began before the mutation click cannot prove Create/Save/Deploy/Delete success.
- Captures post-dispatch CDP requests even before response-body collection completes, while constraining evidence to the same HIP route and execution stage.
- Serializes mutation dispatches inside the persistent browser and arms an in-session quarantine immediately after any physical write-capable click. No second agent/coroutine may mutate until reconciliation proves committed/rejected/not-dispatched.
- Retains the quarantine for response-lost, visible-success-unconfirmed, mixed/partial and otherwise indeterminate outcomes.
- Adds cross-run unresolved-mutation quarantine in the persistent governance ledger. A crashed/indeterminate prior execution blocks an identical replay even when force-repeat is requested; a terminal verified rejection/failed-before-mutation clears the hazard.
- Writes a durable `change_execution_started` marker before LIVE governed execution so a process crash cannot erase the fact that mutation execution may have begun.
- Makes the hash-chained governance ledger append-safe across parallel processes using a bounded stale-recoverable lock file plus flush/fsync.
- Revalidates SSO state, HIP route and exact locator again at the final pre-dispatch boundary after AutoWebGLM planning, preventing mutation against a redirected/rerendered surface.
- Binds nested surface provenance to the page route and clears the full child-surface chain on SPA route drift before any continuation can execute.
- Integrates certified mutation outcomes with the generic ReAct self-heal controller: quarantine/rejected/ambiguous/no-retry outcomes are always `unsafe_or_mutating` and cannot trigger automatic phase replay.
- AutoWebGLM remains the primary decision framework; AgentQ, Edge single-session ownership, multimodal watchdog, semantic affordances, DDS exact commit, surface leases, ancestry, mutation governance and PyAutoGUI last-resort policy remain intact.
- Full regression: 767 / 767 PASS.

# v1.9.7 — Mutation Outcome Reconciliation + No Duplicate Dispatch Guard

- Adds physical mutation dispatch evidence so failures before click invocation are distinguished from exceptions after a mutation may already have reached HIP.
- Prohibits cross-executor fallback/retry after any physical mutation dispatch attempt; MCP, Playwright and PyAutoGUI cannot issue a second Create/Save/Deploy/Delete/Submit-style click for the same uncertain action.
- Adds bounded read-only mutation reconciliation using captured HIP write responses, structural state and value-free visible status-signal hashes.
- Recovers a mutation as committed when a 2xx write response arrives after a browser/UI timeout, without dispatching another click.
- Classifies terminal non-2xx responses as `rejected_verified`, visible success without network proof as `visible_success_network_unconfirmed`, pending/lost responses as indeterminate, and mixed 2xx/error writes as partial change.
- Allows one adaptive rebind only when the runtime proves no physical mutation dispatch occurred and no HIP write request was observed.
- Governed post-change status now distinguishes verified rejection from backend-ambiguous/partial mutation and correctly records whether mutation dispatch was observed.
- Full regression: 757 / 757 PASS.

# v1.9.6 — Nested Surface Ancestry Chain + Continuation Scope Guard

- Carries proven UI provenance across menu -> dialog -> drawer/listbox child-surface transitions instead of resetting scope after each click.
- Maintains a bounded structural surface chain (maximum depth 8) containing selectors/roles/IDs/geometry only; customer-entered values are never stored in the ancestry.
- Re-proves the deepest child before continuation actions such as Next, Back, Save, Create, Close, Edit, Clone, Migrate, Deploy, Delete, Upload and Download.
- While a proven child remains active, a missing continuation target fails closed and cannot fall back to a same-named global page action.
- When a child closes, the runtime prunes only that child and re-proves the still-visible parent, preserving safe wizard/dialog continuity.
- Newly opened child surfaces inherit provenance only through the exact structural delta caused by the proven opener/action; ambiguous child surfaces remain blocked.
- Clears the surface ancestry chain on module navigation so provenance can never cross page-family boundaries.
- Full regression: 753 / 753 PASS.

# v1.9.5 — Continuous Surface Provenance Lease + Stable Target Guard

- Converts detached-overlay provenance from a one-time selector decision into a continuously revalidated lease.
- Re-proves the menu/listbox/popover before every target read and every virtualized scroll.
- Rebinds an Angular/CDK/DDS surface after destroy/recreate only through opener-controlled ID or one uniquely matching structural continuation.
- Treats selector continuity as a weak hint rather than durable identity; stale duplicate overlays cannot silently inherit trust.
- Requires two consistent semantic target reads inside the proven surface before the normal compound-action path accepts the target.
- Performs a final membership proof immediately before click: exactly one visible target must be contained by exactly one currently proven surface.
- Once provenance is transferred to an overlay, no global same-named action may be used as fallback.
- Full regression: 749 / 749 PASS.

# v1.9.4 — Detached Overlay Provenance + Virtualized Option Surfaces

- Transfers semantic scope from a proven row/entity opener into the exact new Angular/CDK/DDS menu, listbox, popover, dialog, or drawer that the opener caused.
- Uses aria-controls / aria-owns when available; otherwise requires an unambiguous new-surface delta.
- Resolves target actions only inside the proven detached surface, preventing similarly named global actions from being selected.
- Adds bounded scroll-only discovery for virtualized/off-screen menu options; no mutation occurs during discovery.
- Fails closed when multiple equally plausible new surfaces appear.
- Keeps generation-safe target re-resolution inside the proven surface immediately before click.
- Full regression: 745 / 745 PASS.

# v1.9.2 — Repeatable-row identity + semantic affordance layer (2026-09-01)

- Repeatable Angular/DDS rows no longer use physical DOM order as durable identity. Committed semantic anchors bind each `input.json` row to the correct live row after `+ Add`, rerender or reorder; blank rows use only provisional order until an anchor is committed.
- Added a generalized semantic-affordance resolver for icon-only actions. It understands local `+`/Add, expand/collapse chevrons, overflow/kebab menus, edit/pencil, clone/copy, migrate/transfer, deploy, delete, next/back, filter, settings, refresh, upload/download and related actions from text, ARIA, title, SVG/icon metadata and local section context.
- A bare global `+` is never trusted as row Add. `+` becomes `add_row` only when section context matches and the click changes the intended row count exactly `N -> N+1`.
- AutoWebGLM remains the primary policy layer for repeatable Add and generalized actions. Deterministic Playwright/DDS adapters execute the approved intent and exact state/effect verification remains authoritative.
- Certified/future-task capability binding now has semantic-affordance fallback for current HIP releases that render row actions as icons rather than labels.
- Mutation affordances (Clone/Migrate/Deploy/Delete/Save/Create) cannot bypass the existing governance authorization gate.

# v1.9.1 — Parent/child DOM-generation barrier (2026-09-01)

- Adds a post-commit DOM-generation barrier after every state-changing select/multi-select/radio/toggle.
- Parent values must survive Angular/DDS rerender and the relevant parent/child binding topology must stabilize for consecutive fresh captures.
- Every graph node is now resolved from a fresh live control capture; cached selectors are evidence only and are never reused as durable locators.
- Conditional children must be visible and bound to the same rebound selector generation for consecutive captures before child execution starts.
- Selector replacement is explicitly recorded in `transaction_proof.post_commit_generation_barrier`.
- Fails closed on lost parent commit or non-stabilizing DOM generation instead of clicking stale controls.
- AutoWebGLM remains the primary browser-decision framework; deterministic HIP drivers remain verified action adapters and final DOM state is authoritative.
- Full regression: 728/728 tests pass.

# v1.9.0 — AutoWebGLM primary framework layer (2026-09-01)

- Promotes AutoWebGLM from recovery-only advisor to the primary browser-decision framework.
- Every normal click/fill and shared DDS single-select/radio interaction is represented as an AutoWebGLM action before a tool executes it.
- Existing HIP deterministic scripts remain the verified tool adapters: they execute the approved intent and prove exact Angular/DDS state.
- Adds intent-alignment gates so model proposals cannot drift away from the vetted phase skill, target control, or requested value.
- If the native/Dell model is unavailable or proposes a conflicting action, the vetted HIP intent is emitted in AutoWebGLM's own action protocol and executed by the deterministic adapter.
- Keeps final Save/Create/Delete/Deploy/Submit-style mutations blocked from unguided AutoWebGLM control.
- Keeps Microsoft Edge as the single browser/session owner and preserves AgentQ reward gating, vision loading recovery, Browser Use, LangChain, MCP and PyAutoGUI fallbacks.

# v1.8.9 — Document Type DDS exact-commit layer (2026-09-01)

- Single-select DDS actions now resolve only the listbox owned by the active combobox via `aria-controls` / `aria-owns`; page-global option clicks are forbidden.
- Enum-style input values such as `TRANSACTION_ROOT_ELEMENT` and `ELEMENT_IN_PAYLOAD` are semantically matched to DDS display labels such as `Transaction Root Element` and `Element In Payload`.
- A blank searchable DDS input is no longer treated as unfilled when the owned listbox/chip contains the exact committed selection.
- Multiple equally good option candidates fail closed instead of selecting an arbitrary version/value.
- Direct Python Playwright is the deterministic first executor; Playwright MCP is a bounded interaction fallback and still relies on locally scoped option resolution and exact commit verification.
- Blind Enter and free-text fallback are prohibited for true DDS single-select controls.
- Document Type control capture follows portalled listboxes outside the `dds-dropdown` host.
- Transaction evidence now contains `single_select_driver_audit` for every Document Type DDS single-select.
- Full regression: 716/716 tests pass.

# 2026-09-01 — v1.8.7 Microsoft Edge + vision loading watchdog + complete AutoWebGLM runtime protocol

- Microsoft Edge Stable is now the primary persistent HIP browser (`msedge`) with a dedicated `data/edge_profile`.
- Added a same-session multimodal `VisionRuntimeBridge` for recovery and page-health perception; it probes image understanding instead of trusting a text-only deployment that merely accepts image JSON.
- A geometrically blocking loader that remains continuously present for >300 seconds must be confirmed by vision before Edge refresh. Passive indicators do not refresh the page.
- Unsaved-form refresh writes only value-free evidence and forces deterministic replay from the current `input.json`; it never serializes customer field values.
- AutoWebGLM bridge now exposes all ten official runtime action types, including `user_input`, and richer bounded field/section context. Research training/benchmark assets remain external and optional.
- Runtime API/JavaScript UI now surface Edge, vision, five-minute loading watchdog and AutoWebGLM protocol readiness.

# 2026-09-01 — v1.8.6 adaptive context + AutoWebGLM + LangChain browser recovery

- Increased normal expert-skill context to 64k chars, failure-only browser recovery to 128k, and Dell AIA self-heal advisor context to 96k while preserving phase-local normal execution.
- Added an AutoWebGLM-compatible recovery bridge using task + value-free simplified HTML + viewport position + bounded action history and a safe one-action parser.
- The AutoWebGLM research checkpoint is optional via `native_model_command`; Dell AIA predicts the protocol by default, so no second model/browser is required.
- Added a real lazy `PlayWrightBrowserToolkit` integration from `langchain-community==0.4.2`, attached to the same authenticated Chrome and restricted to read-only perception tools.
- Aggregated Browser Use + LangChain + AutoWebGLM into one bounded failure-only browser-intelligence recovery bundle.
- Added runtime/API/UI status for the new browser-intelligence layers and timeout/fail-open controls so they cannot introduce a new multi-hour stall.
- Added dedicated AutoWebGLM/LangChain/context regression coverage.

# 2026-09-01 — v1.8.5 Document Type stall guard + persistent learning + AgentQ reward

- Fixed the live three-hour Document Type failure mode: deterministic execution now precedes discovery, and `until_complete` can no longer bypass no-progress/wall-clock guards.
- Wrapped each phase execution in a wall-clock timeout so an inner Playwright/DDS loop cannot monopolize the authenticated browser indefinitely.
- Added value-free stuck-state semantic DOM + screenshot forensics on the existing SSO page.
- Bootstrapped Capabilities/API Contracts from the reviewed Unified Deep KB and added candidate live learning during failed runs.
- Added Source Document Type -> Target Document Type judge-approved same-family replay with no new pre-fill discovery.
- Strengthened AgentQ with reward/success-rate UCB scoring, repeated-loser suppression and a local reward/safety gate over MCP advice.
- JavaScript learning tabs now show canonical/live source and trust plus graph counts.
- Regression suite: 695/695 tests passed before final release packaging.

# 2026-09-01 — v1.8.4 description-triggered expert skills + Browser-Use recovery context

- Implemented five runtime execution principles: description-triggered routing, registered HIP expert skills, phase-local context budgeting, deterministic-first execution, and skill vetting before browser launch.
- Added deterministic description routing for Full, each individual section, Source/Target variants, and exact custom multi-section subsets.
- Added `hip_id_agent/expert_skills.py` with seven phase skills, implementation provenance, deterministic/verification/recovery contracts, context budgeting and pre-run vetting.
- Added FastAPI `/api/mission/description-plan`; `/api/mission/start` can use the description as the authoritative trigger while remaining fail-closed.
- Added JavaScript Description Trigger, Expert Skill Vetting and Context Budget UI surfaces.
- Deepened Browser Use 0.13.8 integration: same-CDP state now produces bounded semantic interactive-element recovery context, reducing broad state/context use while keeping Browser Use non-mutating.
- Added exact arbitrary phase-subset mission command generation for description combinations such as Data Map + Rule only.
- Preserved v1.8.3 exact/stable fills, PyAutoGUI last resort, Full seven-phase continuation, Transport Profile long-path fix, Data Map Version verification-only, DDS committed-state recognition and governance.
- Full regression suite: 686/686 tests passed before release packaging.

# 2026-09-01 — v1.8.3 exact-fill + PyAutoGUI last-resort hardening

- Added optional Windows `PyAutoGUI 0.9.54` as a last-resort physical interaction tool after Playwright MCP and Python Playwright fail on an already-resolved visible control.
- PyAutoGUI is fail-closed for final mutating Save/Create/Delete/Deploy/Submit/Publish/Update clicks by default and never discovers targets independently.
- Added stable browser-window/locator geometry checks before any coordinate action and trusted browser-click verification afterward.
- Added exact double-read post-fill verification so a field is successful only when the requested value remains committed across an Angular/DDS settle interval.
- Added an idempotency guard: an already-correct field is not filled again.
- Added runtime/API/UI status for the optional PyAutoGUI tool and per-run PyAutoGUI audit evidence.
- Preserved v1.8.2 Full-run, Transport Profile long-path, phase-scoped golden-evidence, Data Map Version verification-only and DDS selected-state fixes.
- Full regression suite: 677/677 passed before release packaging.

# 2026-08-31 — v1.8.1 JavaScript UI runtime hardening

## 1.8.2 - 2026-08-31

- Full no-save missions now continue attempting remaining selected phases after a blocked phase while keeping the overall verdict blocked; this prevents a Full run from ending after Data Map.
- Fixed Full mission freshness: a normal Full run no longer silently auto-resumes an older incomplete mission; all seven selected phases start from the current input unless resume is explicitly requested.
- Fixed Transport Profile-only startup on long Windows/OneDrive paths: golden screenshot copies use a short flat cache and safely fall back to the original reference instead of crashing the mission.
- Golden screenshots are now phase-scoped, so a section-only run prepares only evidence relevant to that selected section.
- Fixed Data Map Version churn: Map Identifier Version is verification-only/portal-owned and is never actively refilled by the reconciliation layer.
- Fixed DDS single-select reconciliation: selected option/chip state is authoritative when the inner input value is blank, preventing repeated re-selection of an already committed Version/Contivo/etc. dropdown.
- Added four regression tests for the live issues; full suite is 672 tests.

- Kept the Bun-served JavaScript SPA as the primary UI and FastAPI as the agent backend.
- Hardened direct Chrome DevTools MCP so the library default is fail-closed unless explicitly enabled by project configuration.
- Added bounded MCP request and shutdown timeouts so a silent or stuck child process cannot block mission teardown indefinitely.
- Made `bun run platform` verify FastAPI `/health` before exposing the JavaScript control center; an already healthy backend is reused.
- Preserved Dell-compatible MCP package versions: `@playwright/mcp@0.0.78` and `chrome-devtools-mcp@1.6.0`.
- Preserved AutoGen 0.7.5 existing-venv detection, section-scoped execution, Transport Profile-only execution, dynamic repeatable `+` rows, Browser Use/CDP, AgentQ, evidence capture and governed changes.
- Complete hardened regression inventory: 667/667 tests passed.


## 2026-08-25 — BizFlow Deep Capability Learning
- Added `learn-bizflows-deep`, FastAPI `/api/bizflows/deep/start`, Streamlit **Deep Learn BizFlows**, and `RUN_LEARN_BIZFLOWS_DEEP.ps1`.
- Reused production multi-tab BizFlow transaction logic for Flow Details, Source/Identifiers, Targets, nested Process Steps, Enricher filename rows, Configure Routing, Conditions and Actions.
- Added value-free BizFlow dependency/replay promotion and UI→API causal trace.
- Added safe Migrate/Deploy/Delete prerequisite probes behind a request-abort barrier.
- Full test inventory: 590/590 passed before packaging.
# 2026-08-08 — Full E2E input-contract hardening (512 tests)

- Rechecked the Streamlit + AgentQ UI/API autonomous package against every scalar leaf in the UHAUL `input.json`; all seven phase contracts now require 100% input-to-control/accounting coverage before Chrome opens.
- Added cross-object referential-integrity preflight across Data Map, Document Types, Rule, Transport Profiles and BizFlow so contradictory input cannot be misdiagnosed as a browser failure.
- Completed previously omitted BizFlow controls: Flow Identifier Document Type per row, Mapping Transformer disabled Source Document Type and Add-Rule gate, Enricher disabled Document Type / Target File Name Config / Part Number rows, Routing Status / Execute Always, plus read-only Flow Version and Step Number accounting.
- Added selector-bound checkbox/switch execution and safe disabled/default All/Other verification semantics.
- Streamlit now blocks Start when input coverage, golden screenshots or required upload assets are incomplete and exposes a dedicated Preflight tab.
- Added eight regression tests; complete suite is 512/512 passing.

# 2026-07-21 — Autonomous Mapping Commit Recovery (465 tests)

- Fixed Rule Mapping Identifier DDS interactions where Playwright timed out after the portal had already selected the exact owned option.
- Added selected-state re-probe, safe settle/rebind verification, one owned DOM dispatch fallback, and one exact-single-option Enter fallback.
- Prevented typed-but-unselected Mapping Identifier values from being accepted.
- Added `--autonomous-mission` and `RUN_AUTONOMOUS_MISSION.ps1` for one-switch all-phase until-complete execution with fail-closed auto-resume.
- Full regression suite: 465/465 passed.

# Changelog

## 2026-07-21 — Autonomous Web Agent mission controller (complete-the-application)

- Added `hip_id_agent/mission_controller.py`: crash-safe `mission_state.json` ledger, fail-closed resume adoption, resumable-run discovery, per-run entity registry and a single `mission_completion_report.json/.md` application-complete verdict.
- Added `--resume-run` / `--auto-resume` to `run-full-dummy-fill` and `-Resume` / `-ResumeRunDir` to `RUN_ALL_PHASES_UNTIL_COMPLETE.ps1`; judge-approved completed phases from an interrupted run are adopted without browser replay, everything unproven re-executes live.
- Every judged phase success now persists `phase_verification.json` and `phase_judge_result.json`, making the run adoptable by future resumes.
- Added `browser_disconnected` self-heal classification and the safe `restart_browser_session` action; `BrowserSession.restart()` relaunches the persistent Chrome context after a crash while reusing SSO cookies and preserving learned memory.
- Later phases receive `_mission_prior_entities` so the whole application references one consistent entity set per run; the registry stays value-scoped to the run and is never promoted into the portal brain.
- Regression suite: 447 existing tests plus 14 new autonomous-mission tests.

## 2026-07-21 — All phases until-complete forensic self-heal

- Added `--all-phases-until-complete` for Data Map, Source/Target Document Type, Rule, Source/Target Transport Profile and BizFlow.
- Added full local Playwright, Playwright MCP and Chrome DevTools MCP failure evidence bundles.
- Added golden-image diagnosis to every failed recovery cycle for every phase.
- Added judge-gated, value-free deterministic trajectories after every successful phase.
- Flow Pattern Memory now persists stable selector and interaction profiles, not only semantic binding identities.
- Added `RUN_ALL_PHASES_UNTIL_COMPLETE.ps1`.
- Complete regression suite: 447 tests.

## 2026-07-20 — Rules async Mapping Identifier and existing-name structural fix

- Added owned, asynchronous DDS selection for Mapping Identifier Name (Version).
- Supports exact off-screen options in large Rule mapping lists.
- Added unsaved temporary Rule Name transaction when the exact name already exists.
- Restores and verifies the exact input Rule Name after repeatable rows are created.
- Accepts existing Rule Name validation only when read-only inventory resolves the exact object.
- Expanded mandatory Conditions failure evidence.
- Test suite increased from 434 to 438 tests.

## 2026-07-20 — Rules global-validity gate before Conditions +

- Fixed live no-op Conditions `+` clicks when the first Condition was exact but required Rule/Action fields were still blank.
- Added an exact prerequisite transaction for Rule Name, Document Type, Description, default Action Name/Type and Mapping Identifier before adding a second Condition.
- Added foreground Angular form-validity evidence and fail-closed behavior for known `ng-invalid` Create Rule forms.
- Changed the Conditions click probe to capture nested `add-cir` icon activations even when DDS stops bubbling or replaces the internal button.
- Added regressions for action-before-condition ordering, invalid-form refusal and nested-icon click evidence.
- Acceptance: 434 tests passed.

## 2026-07-20 — Course AgentQ Architecture + HIP Intelligence MCP

- Added a value-free Web Representation Model for DOM/accessibility/Angular/DDS/MCP evidence.
- Added hierarchical per-phase plans with verification after every field task.
- Added a bounded UCB-style Action Model and deterministic transaction critic.
- Added success/failure trajectory memory and value-free action preference pairs.
- Added a local HIP Intelligence MCP server with representation, planning, critique, drift and memory tools.
- Integrated learned control-identity priors without weakening live one-to-one binding gates.
- Kept Playwright MCP and Chrome DevTools MCP on the same authenticated browser; no MultiOn dependency.
- Added tests for architecture mapping, memory safety, MCP round trips and learned binding priors.

## 2026-07-18 — All-phase advanced selection rules

- Added exact-set DDS multi-select transactions with additive preservation and safe explicit removal.
- Forbid blind Select All, blind Enter, bulk clear and empty-search Backspace.
- Added typeahead exact-option commit, virtualized-option stability, radio exclusivity and checkbox checked-state rules.
- Applied advanced widget contracts to all seven phases.
- Replaced Transport Profile direct radio/checkbox property mutation with shared explicit Playwright actions.
- Acceptance: 388 tests passed.

## 2026-07-17 — Single-session self-heal log reconciliation

- Fixed false Usage multi-select failures when Dell drops labels from repeated rows after chips render.
- Added structural, row-scoped semantic inference and exact selected-set reconciliation.
- Added unique-phase versus retry metrics to persistent-session logs.
- Added run 024916 regression tests.

# Runtime Agentic Self-Heal Loop — 2026-07-17

- Added a bounded phase-level ReAct self-heal controller around both execution exceptions and independent judge failures.
- Captures Playwright MCP, Chrome DevTools MCP, Python Playwright, DOM, mutation, network, console, screenshot and Portal Brain evidence before every repair.
- Classifies SSO expiry, route failures, MCP drift, overlays, lost form surfaces, missing controls, exact-value failures, multi-select errors, repeatable-row errors, upload errors, judge conflicts and transient timeouts.
- Applies only safe no-save repairs and reruns only the interrupted phase from the exact phase-local input JSON.
- Stores repairs as candidate Portal Brain knowledge and promotes them only after deterministic + text + vision judge approval.
- Adds repeated-failure signatures, total repair budgets, per-phase attempt limits and fail-closed loop prevention.
- Adds optional Dell AIA recovery advice constrained to the deterministic action allow-list.
- Acceptance: 319 tests passed.

# Adaptive KB Self-Healing Update

- Added live Playwright-MCP revalidation of known and unknown parent-value branches.
- Added versioned KB repair proposals, applied corrections, suspect conflicts and rollback-safe audit history.
- Added same-run live exploration overlay for deterministic plans.
- Added repeated-proof supersession for wrong canonical dependencies.
- Added self-healed KB/KG export while preserving original reviewed files.
- Added `kb-repair-status` and `export-self-healed-kb` CLI commands.
- Acceptance: 231 tests passed.

# Changelog

## Rules KB true deep-learning patch

- Fixed Rules UI row-action JavaScript to use `document.*` instead of undefined `rule.*`.
- Prevented `/api/rule/summary` listing rows from being counted as deep profiles.
- Added drawer/menu cleanup before `+ Add Rule` form capture.
- Added regression tests for summary-vs-detail profile handling and JS scope.
- Validation: `132 passed`.


## Document Type full-run patch

- Kept Data Map functionality unchanged and scoped this patch only to SecureLink Document Type KB learning.
- Changed `discover-doctype-kb` default `--max-api-pages` to 250 for large Document Type inventories.
- Fixed the Document Type learner default so omitted `--max-detail-rows` means all discovered Document Type rows, not `max_api_pages`.
- Renamed internal Document Type detail matching/candidate-limit wording from map-oriented names to doctype-oriented names.
- Regression suite now reports 118 passed.


## v-data-map-blank-page-retry-fixed

- Prevented double navigation after SSO lands on `/hybrid-integrations/securelink/datamaps`, which could abort Angular chunks and leave a blank white page.
- Added Data Maps readiness watchdog: waits for old map rows, Add button, or `/api/mac-map` network evidence before continuing.
- Added retry/reload recovery for blank Data Maps SPA shells.
- Added authenticated direct fetch fallback for `GET /inaas-gateway/hipService-svc/api/mac-map/summary` so old Data Maps can still be captured even if UI/network capture misses the listing call.
- Included direct fallback API interactions in the final Data Map API KB and Knowledge Graph.
- Preserved pagination/detail enrichment audit in the upload summary.

# Changelog

## full_inventory_retry_audit_fixed

- Added retry/backoff for transient gateway failures during `export-all`.
- Replays discovered `x-requester-id` consistently and adds BizLink Partner/System referer headers.
- Treats all non-OK/non-200 inventory API responses as errors instead of silently continuing.
- Adds `inventory/failed_requests.json` and `inventory/inventory_request_audit.json`.
- Attempts read-only UI fallback exploration when API fan-out has failed child endpoints.
- Changes final stage status to `partial_success` when collection is incomplete due unresolved failed requests.
- Keeps extraction read-only; unsafe actions are still skipped.

## Full Inventory Partial-Status Fix

- Fixed `export-all` reporting `failed` when inventory was actually exported with only some unresolved live gateway fan-out 500s.
- Added explicit `ctx.registry["run_status"] = "partial_success"` when failed fan-out requests remain.
- Added `inventory_failed_request_count` in the run registry.
- Updated `ReportWriter._infer_status()` to treat `partial_success` as a valid non-fatal status.
- Added regression tests for partial inventory run status.

## full_inventory_detail_tree_fixed

- Added full detail-row preservation in `InventoryEntity.extra.details` for every Account, Partner, Domain and System row captured from Network payloads.
- Added hierarchical detail outputs:
  - `accounts_with_partners_details.json`
  - `domains_with_systems_details.json`
  - `complete_inventory_tree.json`
- Added targeted UI fallback for failed Account→Partner fan-out calls: searches the parent Account, opens its card menu, clicks Show Partner(s), and extracts all Partner rows from the resulting Network calls.
- Added targeted UI fallback for failed Domain→System fan-out calls: searches the parent Domain, opens its card menu, clicks View Domain/System(s), and extracts all System rows from the resulting Network calls.
- Preserved read-only safety: no Save, Update, Delete, Add, Create, Submit, Remove, Reset, Enable/Disable actions are clicked.
- Added regression tests for full detail preservation and hierarchy tree output.

## v-full-inventory-api-id-catalog
- Added first-class Deployment Group inventory extraction from `/domain-systems/domains/<domainId>/deployment-groups` because TP APIs need `source_deployment_group_id` and `target_deployment_group_id`.
- Added `api_required_ids_candidates.json`, `api_required_ids_candidates.csv`, and `api_id_lookup_by_name.json` to map Partner/System inventory into the user's required API ID checklist.
- Added domain -> deployment group relationships and included deployment groups under each domain in `complete_inventory_tree.json`.
- Kept Document Type, Map, Rule, Workflow and TP IDs clearly marked as not available from Partner/System inventory so the next phase can target the right pages/APIs.

## Full Partner/System API-ID inventory fix

- Added exhaustive read-only UI nested walk for `export-all`.
- Added `--full-ui-nested-walk / --no-full-ui-nested-walk`.
- Added `--enrich-link-details / --no-enrich-link-details`.
- Added parent search fallback terms using Account/Domain `componentId`, name, and ID.
- Added synthetic title-card fallback to handle DDS cards that appear in text but fail bounding-box discovery.
- Added best-effort detail-link fan-out for Accounts, Partners, Domains, Systems, and Deployment Groups.
- Preserves API fan-out and all existing inventory files.

## v1.1.1 - Full Inventory Progress/Heartbeat

- Added terminal progress bars for `export-all` so long Account -> Partner and Domain -> System crawls show current phase, completed/total parents, percent, and current counts.
- Added durable progress files under each run:
  - `inventory/progress.json`
  - `inventory/progress_events.jsonl`
  - `inventory/progress_heartbeat.txt`
- Progress now covers browser/SSO warm-up, System API fan-out, Partner API fan-out, exhaustive UI nested parent walk, detail-link enrichment, final file writing, and memory save.
- Progress reporting is non-fatal: if progress-file writing fails, extraction continues.

## Progress/Stuck watchdog patch
- Added bounded, heartbeat-updated final log flushing so `export-all` no longer appears stuck at `finalizing | Flushing browser/network logs` for large Network captures.
- Browser shutdown is now idempotent and best-effort; logs flush only once and Playwright close/stop calls are timeout-bounded to avoid Windows `EPIPE`/closed-pipe shutdown noise after interruption.
- Inventory API `fetch()` calls now use an AbortController timeout and Python-side timeout so one Dell gateway call cannot block the whole inventory run indefinitely.
- Large `network_tab_events.json` / `network_events.jsonl` writes are streamed instead of building huge indented JSON strings in memory.

## v80.0-summary-agent

- Added `InventorySummaryAgent` for small upload/review packs.
- `export-all` now writes `UPLOAD_THIS_SUMMARY.zip` automatically before final memory/report/KG steps.
- Added `python -m hip_id_agent.cli summarize-run <run_dir>` to summarize existing huge runs without rerunning.
- Raw Network evidence is compact by default; set `HIP_WRITE_FULL_NETWORK_LOGS=1` only when full Network dump is required.
- Full Knowledge Graph evidence is skipped by default for `export-all`; use `--write-heavy-evidence` only when needed.
- Avoids clicking the Dell DDS `Filter` button by default; set `HIP_ALLOW_FILTER_BUTTON_CLICK=1` only for portal variants requiring it.


## Data Map KB Discovery Patch

- Added `discover-datamap-kb` command for SecureLink Data Maps.
- Navigates to `/hybrid-integrations/securelink/datamaps`, clicks `+ Add`, captures form controls/dropdowns/buttons/required fields/DOM event hints, and fills dummy values without saving.
- Added compact upload artifact `UPLOAD_DATAMAP_KB_SUMMARY.zip` so raw network/KG evidence does not create oversized uploads.
- Added previous-interaction extraction from `objects.data_map` input JSON and optional `--known-map-id`.
- Added regression tests for Data Map seed extraction, dummy value generation, field mapping, and prior ID context.

## v80.XXX Data Map full API learning + old inventory
- `discover-datamap-kb` now first opens the SecureLink Data Maps list and learns the exact APIs triggered by the portal before clicking `+ Add`.
- Extracts old/existing Data Map IDs and details from observed Network responses: `mapId`, `mapIdentifier`, version, status, map name, map class, Contivo version and map data file/JAR.
- Replays observed paginated listing APIs when pagination parameters are present, bounded by `--max-api-pages`.
- Saves API learning evidence: request URL/method/status/headers, request body, response shape, extracted row counts and sample rows.
- Then clicks `+ Add`, captures form controls/dropdowns/required fields/DOM events, fills dummy values, and never clicks Save/Create/Submit.
- Adds compact upload zip outputs and tests for old Data Map extraction.


## v80.XXX Data Map API Flow Knowledge Graph
- Added compact Data Map API Flow Knowledge Graph generation during `discover-datamap-kb`.
- New outputs: `datamap_api_flow_knowledge_graph.json`, `.html`, `.mmd`, and `.md` under `runs/<RUN_ID>/datamap_kb/`.
- The graph connects Data Maps page load, observed listing APIs, endpoints, compact request headers, response shapes, old map ID rows, pagination replay, `+ Add` click, Add-form fields/dropdowns, DOM events and dummy-fill attempts.
- Graph stays uploadable by default and never embeds full raw network bodies.

## datamap_api_flow_kg_label_collision_fixed

- Fixed `TypeError: add_node() got multiple values for argument 'label'` in Data Map API Flow KG generation.
- Root cause: live DOM/dummy-fill audit rows can include a `label` property while the KG builder also used `label` as a positional argument.
- Changed KG builder internals to use `display_label` for node label and keep raw DOM `label` in properties.
- Added regression coverage for dummy-fill attempts containing `label`, `type`, and selector metadata.

## Data Map numeric ID enrichment patch

- Added read-only detail ID enrichment for old Data Maps.
- Added known prior mapId merge for the U-HAUL seed map.
- Added `old_datamaps_inventory_with_ids.json/csv`.
- Added `datamap_detail_enrichment_audit.json`.
- Added `datamap_id_completion_report.json`.
- Added KG nodes for detail API enrichment attempts.
- Added `--max-detail-rows` CLI option.
- Improved Data Map normalization for `latestDevVersion`, `mapClassName`, and `availableEnvironments`.


## v80.0xx - Data Map old map UI action ID learning
- Added second-phase old Data Map ID discovery using the real UI row/action flow, similar to Partner/System discovery.
- For each old Data Map row, the agent can search the row, click safe View/Edit/Details/Open actions, capture the triggered API, and extract numeric mapId from the response/URL when exposed.
- Added checkpointed output: datamap_ui_row_action_enrichment_audit.json and ui checkpoint files.
- Data Map API Flow KG now links listing API, detail API probes, UI row actions, triggered APIs, Add form, dropdowns, required fields, and dummy fill events.
- Save/Create/Submit/Delete actions remain blocked.

## Document Type KB discovery phase

- Added `hip_id_agent.doctype_kb.DocumentTypeKBFlow`.
- Added CLI command `discover-doctype-kb`.
- Added SecureLink Document Types URL: `https://developer.dell.com/hybrid-integrations/securelink/doctypes`.
- Added API/listing extraction for old Document Types.
- Added pagination replay and authenticated direct summary fallback candidates.
- Added bounded read-only detail enrichment for numeric `documentTypeId`.
- Added UI row/action/expand learning phase for hidden IDs.
- Added Add Document Type form capture: controls, required fields, dropdowns, DOM event hints.
- Added dummy fill without Save/Create/Submit.
- Added Document Type API Flow Knowledge Graph JSON/HTML/Markdown/Mermaid exports.
- Added uploadable summary ZIP: `UPLOAD_DOCTYPE_KB_SUMMARY.zip`.
- Added regression tests in `tests/test_doctype_kb.py`.

## 2026-07-06 - Document Type Add overlay recovery

- Fixed Document Type KB run crash after full UI row learning when Dell DDS loading overlay intercepted the final `+ Add` click.
- Added computed-style overlay detection for `.dds__loading-indicator__overlay` and Angular `app-loadingindicator` overlays.
- Added overlay-aware retry to generic `click_and_wait`.
- Added Document Type-specific `+ Add` recovery with read-only DOM fallback.
- If Add form cannot open, the run now writes inventory/API/UI-row evidence as `partial_success` instead of losing the run.
- Prevented listing page controls from being misreported as Add-form controls when Add form fails to open.
- Local tests: `119 passed`.


## Document Type KB form-scope cleanup

- Scoped Add Document Type form control capture away from global page controls.
- Filtered listing pagination/search, audit modal, cookie preference controls, footer/nav noise.
- Cleaned Data Format Type dropdown options so Home/BizLink/SecureLink etc. are not captured as valid values.
- Added post-fill dropdown recapture/merge for dependent fields.
- Tests: 121 passed.

## Deep Profile Capture Patch

- Added full per-Document-Type deep profile capture after inventory/ID learning.
- Parses observed `/api/document-type/{documentTypeId}/details` payloads.
- Normalizes `documentTypeDetail`, `flowDetail`, `ruleDetail`, and `tpDetail` into KB evidence.
- Adds attributes, document identifier rows, root element, schema file, document version, and related usage evidence to each Document Type record.
- Adds checkpointed outputs: `old_doctypes_deep_profiles.json`, `doctype_deep_profile_enrichment_audit.json`, and `doctype_deep_profile_report.json`.
- Added CLI options `--capture-deep-profiles/--no-capture-deep-profiles` and `--max-deep-profile-rows`.
- Acceptance: `123 passed`.

## Rules KB phase

- Added `hip_id_agent.rules_kb.RuleKBFlow`.
- Added CLI command `discover-rules-kb`.
- Added read-only Rules inventory, ID enrichment, UI row-action learning, Add-form capture, dummy-fill, and deep-profile capture.
- Added normalization for `ruleConditions[]` and `ruleActions[]`.
- Added tests for Rule summary extraction, deep profile normalization, details URL generation, and dummy-fill safety.
## Rules KB deep profile fix

- Added the missing `_capture_rule_deep_profiles` implementation.
- Rules deep learning now mirrors Document Type: reuse observed detail network payloads, then call read-only details APIs by ID/name/version/environment.
- Added broader Rule detail URL candidates and generic Rule-shaped `id` extraction.
- Added checkpoint outputs for deep profile rows, audit, and report.
- Added regression coverage for UI-network deep profile reuse.
- Validation: 129 passed.


## 2026-07-16 — Portal Brain legacy selector migration

- Fixed deterministic-plan compilation crash when persistent selector evidence was stored as a string rather than a metadata object.
- Added backward-compatible normalization for selector dictionaries, lists, direct records and plain strings.
- Added safe conversion of historical selector evidence counts.
- Preserved semantic-first priority and rejection of dynamic DDS selectors as durable primary locators.
- Added regression coverage for the exact live traceback; full suite now passes 239 tests.

## 2026-07-16 — Dell AIA multimodal preflight compatibility
- Reused existing BASE_URL and Dell authentication for vision.
- Added GEMMA_MODEL_NAME/GEMMA_MODEL and plural candidate aliases.
- Added bounded capability-probed Gemma/Pixtral auto-discovery.
- Added `vision-preflight` CLI command.
- Kept gpt-oss-120b forbidden as an implicit vision fallback.
- Added strict pre-portal diagnostics and regression tests.

## 2026-07-16 — Document Type active-surface and semantic-reacquisition fix

- Added strict Create Document Type surface proof and automatic safe reopen.
- Removed page-corner clicks from Document Type dropdown discovery.
- Restricted repeatable-row Add discovery to compact actionable buttons/links and required an exact +1 row-count effect.
- Added semantic DDS control reacquisition after Angular rerenders.
- Scoped exploration input lookup to the current phase; Document Type values can no longer be sourced from Rule/BizFlow.
- Made exploration restore failures fail closed and stored failed exploration only as negative evidence.
- Filtered background unchecked checkbox values from artifact judging.
- Fixed AutoGen event-loop/client cleanup warnings.

## 2026-07-16 — Stateful parent/child knowledge-graph runtime

- Replaced empty-form/flat-list execution with phase-local target-branch state graphs.
- Added live parent commit → child rediscovery → exact child fill for all HIP phases.
- Added DDS multi-select exact set selection and radio handling.
- Connected all BizFlow tabs and Configure Routing to the shared graph executor.
- Added judge-bound state-graph execution to every fast replay blueprint.
- Added Portal Brain target-branch knowledge and live dependency-edge ingestion.
- Local verification: 279 tests passed. Live authenticated no-save rerun still required.

## 2026-07-16 — Data Map semantic state-graph reconciliation

- Fixed false-negative Data Map termination after exact values were already committed.
- Generic resolver now prioritizes live semantic keys and supports nested fieldset aliases/roles.
- Added exact DDS switch verification and final live-control reconciliation.
- Removed invalid DOM-transition logging from `wait_ready()`.
- Added real-run-shaped regression tests.

## 2026-07-16 — Document Type Usage exact multi-select
- Fixed ambiguous DDS option selectors.
- Prevented Delete/Backspace from removing selected chips on empty Usage inputs.
- Added exact four-value selected-set verification for Source and Target Document Types.
- Full suite: 295 passed.

## 2026-07-16 — Document Type Usage row-scope reconciliation
- Fixed repeatable-row indexing by using the direct fieldset row container instead of nested per-field flex wrappers.
- Prevented Usage graph nodes from resolving to Attribute Name controls.
- Removed cosmetic DDS count labels such as `4 selected` from exact selected sets.
- Applied the same correction to live capture and saved-DOM judging for Source and Target Document Types.
- Full suite: 298 passed.

## 2026-07-16 - Vision judge input-bound reconciliation
- Prevent generic background checkbox/radio/dropdown claims from vetoing exact deterministic passes.
- Require visual issues to bind to an expected field, input path, selector, row, or section.
- Strengthen vision prompt to ignore controls outside the active form/drawer.
- Preserve concrete visual mismatches as fail-closed blockers.

## 2026-07-16 - Single SSO persistent full-run browser

- Full dummy-fill now uses one persistent Chrome context for all phases.
- Playwright MCP and Chrome DevTools MCP attach once and remain attached.
- SSO is requested only when the live shared session is unauthenticated or expired.
- Standalone phase commands keep their prior owned-browser behavior.
- Added root and per-phase browser-session reuse evidence.
- Re-enabled evidence flushing for every borrowed phase.
- Full test suite: 305 passed.

## 2026-07-17 — Agentic ReAct navigation, KB planning and judge separation

- Added a bounded Plan → Act → Observe → Judge navigation controller for every HIP phase.
- Separated executor agreement from requested-target commitment.
- Prevented authenticated Data Maps from being accepted as Document Types.
- Added route replan, MCP resynchronization, transient cleanup and force-commit recovery.
- Fixed missing `mask_sensitive_string` import in phase recovery.
- Added recoverable navigation classification without repeated SSO.
- Added `navigation_react_trace.json` evidence and regression tests.

## 2026-07-17 - Two-minute portal loading watchdog

- Added continuous phase/URL-scoped loading timer.
- Waits for Angular/DDS loading to complete.
- Refreshes the current page once after 120 seconds.
- Preserves the persistent Chrome/SSO context and MCP attachments.
- Reinstalls DOM observers and verifies dual-MCP surface after refresh.
- Removed premature loading-overlay pointer-event bypass.
- Added `refresh_page_and_reopen` self-heal action.
- Added fail-closed handling when loading survives the permitted refresh.

## 2026-07-17 — False loading detection and autonomous page health

- Passive DDS spinner/progress/aria-busy markers no longer block actions.
- Loading requires geometry/pointer/target-hit evidence and two consecutive samples.
- Added pre-action autonomous page-health decisions and proactive route recovery.
- Fixed BizFlow's separate broad loading wait.
- Added bounded `false_loading_marker -> reassess_page_health` self-heal path.

## 2026-07-17 — All-phase agentic runtime coverage
- Enforced the persistent-session + KB/ReAct + MCP + exact-judge + bounded-self-heal lifecycle for Data Map, both Document Types, Rule, both Transport Profiles and BizFlow.
- Added explicit phase contracts and per-attempt agentic preflight evidence.
- Added regression tests proving every configured phase is covered.

## 2026-07-18 — Startup SSO preflight resume and self-heal boundary

- Treats ReAct `sso_required` as a resumable authentication state during the first all-phase preflight.
- Continues the same Data Map attempt after Dell SSO completes in the persistent Chrome context.
- Moves `prepare_phase_attempt()` inside the bounded phase exception/self-heal transaction.
- Records whether a failure occurred in `agentic_preflight` or `phase_execution`.
- Adds regression coverage for `about:blank → Dell SSO → authenticated target` and preflight self-healing.

## 2026-07-18 — Source Document Type repeated-fill / reporting-only replay fix

- Fixed duplicate `order` and `node_id` property collisions in KG builders.
- Made Document Type, Rule, Transport Profile and BizFlow reporting exports non-blocking.
- Added phase exact-completion checkpoints before any reporting-error replay.
- Reporting-only failures now refresh evidence and rejudge without reopening/refilling.
- Added supplied-run regression and full suite coverage (349 passed).

## 2026-07-18 — Deep Portal Learning + MCP Capability Broker

- Added `PortalLearningRuntime` across all seven phases.
- Captures before/after Python DOM models, Playwright MCP accessibility snapshots, and Chrome DevTools MCP DOM snapshots.
- Learns value-free API request/response schemas, validation rules, navigation/action/API links, DOM state transitions, console signatures, storage key names, coverage gaps, and portal drift.
- Added dynamic use of Chrome DevTools MCP performance tracing on retries when the installed tool version supports it.
- Added Playwright MCP read-only evaluation and storage-key-name observation.
- Added persistent Portal Brain stores for page fingerprints, API contracts, validation rules, state transitions, console signatures, coverage, drift, and learning runs.
- Promotion remains judge-gated; failed or unjudged observations remain candidate/negative evidence.
- Added an information-gain agenda from safe controls not exercised in the judged path.
- Added untrusted-page-content guards to GPT-OSS-120B planning, exploration, and self-heal prompts.
- Deliberately did not launch a third browser MCP/controller, preserving the single Chrome/SSO context.
- Added `tests/test_deep_portal_learning_runtime.py`.
- Full suite: 354 passed.

## 2026-07-18 — Document Type structure-first fill-once

- Moved Document Type exploration before business-value filling.
- Rebuilds one clean target form after learning and fills the exact input graph once.
- Freezes completed Document Type forms; no post-fill exploration/dropdown clicking.
- Made Version verification-only and non-mutable.
- Fixed Document Identifier Operation/data-row indexing.
- Deduplicated repeated DDS parent contracts.
- Added canonical text/vision judge field aliases.
- Prevented judge/evidence disagreements from reopening an exact-completed Document Type phase.

## 2026-07-18 — Document Type filled-form evidence lock
- Fixed exact-fill freeze condition that skipped final DOM and screenshot capture.
- Kept post-fill control exploration disabled while making read-only judge evidence mandatory.
- Added immutable filled-form evidence lock and screenshot fallback.
- Prevented completed Document Type replay on evidence-capture failures.

## 2026-07-18 — Document Type DOM-State Transaction Intelligence

- Replaced highest-score-only Document Type control resolution with auditable confidence-margin binding.
- Added Angular/DDS framework metadata, semantic DOM paths and hit-tested interactability.
- Added one-to-one graph-to-form state model and duplicate binding protection.
- Added stable two-sample action transactions and protected prior-field mutation detection.
- Added persistent Portal Brain learning for binding evidence and transaction proofs.
- Added six focused regressions; full suite passes 367 tests.

## 2026-07-18 — All-Phase DOM State Transactions

- Extended confidence-bound semantic control binding from Document Type to Data Map, Rule, both Transport Profiles and BizFlow.
- Added Angular/DDS framework metadata, component identity, geometry and hit testing to the generic control collector.
- Added stable two-observation action transactions and protection of previously committed fields.
- Added one-to-one final form-state enforcement for every phase.
- Added `phase_exact_state_lock.json` and judge no-replay behavior for all completed phases.
- Added all-phase transaction regression tests; full suite 373/373 passes.

## 2026-07-18 — All-phase interaction policy and validated fast replay

- Added structural-parent-first hidden-field resolution for all phases.
- Enforced explicit browser click/check events for radio, switch, dropdown and multi-select widgets.
- Added conditional-child visibility gates after parent interactions.
- Added bounding-box/animation stability, center hit-test and blocking-validation checks before commit.
- Added stale Angular/DDS node semantic rebind and parent re-commit recovery.
- Added conservative learning vs validated-fast-replay execution profiles.
- Added run-level and per-action policy evidence.
- Full automated suite: 379/379 passed.

## 2026-07-18 — Universal HIP Form Policy + Same-Flow Pattern Memory

- Extended the mandatory form-interaction policy beyond the seven creation phases to every known HIP form family: Account, Partner, System, Domain, Deployment Group, Data Map, Document Type, Rule, Transport Profile, BizFlow, Workflow, TP Orchestration and Flow Orchestration.
- Added a generic `/hybrid-integrations` fallback so newly introduced Dell forms still receive structural-parent, explicit-event, visibility, animation, hit-test, validation and mutation-protection gates.
- Added `FlowPatternMemory`, a judge-gated, value-free memory of form topology, action order, parent-child dependencies, repeatable-row shape and successful semantic binding identities.
- Same-family validated flows can enable fast replay across phases (for example Source TP → Target TP) when structural similarity passes the configured threshold.
- Customer values, IDs, credentials, tokens and upload contents are never stored or reused by flow-pattern memory.
- Added universal BrowserSession preflight and candidate action observation for generic Partner/System/Account/Domain exploration paths.
- Added form-family classification to portal-learning observations and policy manifests.
- Added 9 new regression tests; complete suite now passes 397/397.

## 2026-07-18 — All-phase judge evidence reconciliation

- Fixed `pass_with_warnings` being treated as deterministic failure.
- Preserved fail-closed behavior for `failed` verification.
- Enabled exact field-bound DOM evidence to neutralize unsupported GPT and Gemma contradictions.
- Added exact regression coverage for run `UHAUL-POASN-FULL-DUMMY-20260718-153150`.

## 2026-07-18 - Unsaved form stale-overlay recovery
- Prevented loading-watchdog reloads from destroying unsaved HIP drawers/wizards.
- Added safe DDS overlay neutralization with target hit-test verification.
- Added all-phase active-form surface validation before state-graph binding.

## 2026-07-20 — Document Type structure-probe drawer reset

- Fixed clean-form rebuild after Document Type structure learning.
- Replaced same-route navigation as the primary drawer-reset mechanism with an explicit safe Back/Close and discard transaction.
- Added exact listing-toolbar restoration proof before reopening `+ Add`.
- Added a bounded second close attempt for DDS dropdown click interception.
- Added fail-closed behavior before customer-value entry when reset proof fails.
- Added three live-regression tests; complete suite is now 422 tests.

## 2026-07-20 — Rules Attribute dependency and owned-popup fix

- Committed Rule Document Type Name (Version) before dependent Conditions fields.
- Added strict Attribute Name/Unit selection scoped to the exact combobox-owned DDS popup.
- Removed page-global option fallback for Rule condition attributes.
- Added fail-closed handling when the document-type prerequisite is missing or uncommitted.
- Added three live-regression tests; complete suite now contains 425 passing tests.

## 2026-07-20 — Rules physical row and Attribute rebind

- Detect Conditions rows from visible nested Condition Type comboboxes instead of zero-sized Angular/DDS hosts.
- Rebind the Angular row after Attribute option selection and verify the fresh live control value.
- Avoid Escape on a focused but collapsed DDS combobox to prevent delayed popup-open races.
- Added three live regression tests; full suite is now 428 tests.

## 2026-07-20 — Document Type single-select commit protection

- Fixed DDS single-select snapshots that classified an entire mounted option universe as selected.
- Enforced at most one selected value for Document Type single-select controls.
- Settled committed DDS parents before conditional-child discovery.
- Added one bounded sticky-value restore before completed-field mutation failure.
- Added three regressions for Data Format preservation, exact single-selection evidence, and conditional-child settlement.
- Complete suite: 431/431 passed.

## 2026-07-21 — Rules-only until-complete self-heal

- Added `--rules-only` focused execution mode.
- Added `--runtime-self-heal-until-complete` with unbounded safe retry semantics.
- Added exploration/exploitation recovery cycling.
- Added per-failure golden-image vision feedback to the Dell AIA recovery context.
- Added a 60-second Angular conditional-control wait for Rules Mapping Identifier.
- Added global DDS loading-overlay awareness during owned Mapping option selection.
- Added asynchronous duplicate Rule Name settling before structural Conditions creation.
- Added `RUN_RULES_UNTIL_COMPLETE.ps1`.
- Test suite increased from 438 to 443 tests.

## 2026-07-22 — Dependency-aware autonomous parent–child agent

- Added a shared parent–child dependency DAG scheduler for every HIP phase.
- Added phase-level entity dependencies from Data Map and Document Types through Rule, Transport Profiles and BizFlow.
- Enforced exact parent commit before child mount, rebind and fill.
- Enforced complete row-N verification before row-N+1 creation/fill.
- Made judge-validated memory order advisory to dependencies, never authoritative over them.
- Added value-free dependency contracts and node wait profiles to deterministic trajectories and Flow Pattern Memory.
- Added dependency scheduler evidence to every self-heal forensic bundle and Dell AIA recovery prompt.
- Added fail-closed dependency-cycle classification.
- Added 11 regressions; complete suite is now 476 tests.


## 2026-07-22 — Maximum-observability autonomous completion gate

- Added structural evidence for visible and hidden controls, DDS/ARIA ownership, section/row ancestry and mounted options.
- Added capture-phase mutation and UI-event timelines for every autonomous attempt.
- Added sanitized DOM structure snapshots that remove scripts, credentials, query strings and form values.
- Added resource/navigation timing, action inventory, popup inventory and storage-key-name evidence.
- Added fail-closed input-to-control coverage for every required actionable state-graph node.
- Added deterministic replay readiness scoring and blocked memory promotion when coverage/dependency proof is incomplete.
- Added maximum evidence on both failed attempts and successful judged phases.
- Added `--maximum-observability` and `RUN_MAXIMUM_OBSERVABILITY_AUTONOMOUS_MISSION.ps1`.
- Added 7 regressions; complete suite is now 483 tests.

## 2026-07-30 — AgentQ UI/API autonomous form mission

- Integrated the Dell.com crawler's web representation, deterministic safety gate, AgentQ actor/critic ranking, UCB exploration/exploitation, mutation-settle observation and durable trajectory memory into every HIP phase.
- Added observed form-open and form-fill API contract extraction from the authenticated browser network stream.
- Added exact submit-payload capture behind a Playwright route-abort barrier; POST/PUT/PATCH/DELETE cannot reach the backend during capture.
- Added redacted API catalogs, request/response shapes, OpenAPI, Postman and input→UI→API crosswalk exports.
- Added capture, dry-run, validation and explicitly confirmed API-write modes. Write requires both `--allow-api-mutation` and `HIP_ALLOW_API_MUTATION=YES`.
- Exact request values and authorization headers used for a confirmed API replay remain process-memory-only and are never persisted.
- Added `RUN_AGENTQ_UI_API_AUTONOMOUS_MISSION.ps1` and regression coverage.


## 2026-08-04 — Streamlit mission UI and form API payload/response ledger

- Added `streamlit_app.py`, `hip_id_agent.streamlit_dashboard` and `RUN_STREAMLIT_UI.ps1`.
- Added mission launch/stop, input upload, phase monitoring, console tail, evidence download and resume controls.
- Added transaction-level request payload, response status/body, stage, MIME and capture-status evidence for form-open and UI-fill APIs.
- Extended CDP response capture to all XHR/fetch traffic and mutating methods, including non-JSON MIME types.
- Added per-phase `form_api_payload_response_bundle.json` and coverage indexes.
- Kept submit capture fail-closed: mutation request is aborted; a submit response exists only in explicitly confirmed write mode.
- Added eight regressions; complete suite is now 504 tests.

## 2026-08-25 — HIP Browser Intelligence Platform
- Added persistent semantic HIP Capability Graph and API contract graph.
- Added safe five-family `learn-hip` discovery mission.
- Added Search/row-expand/revealed-action/Add-form learning with maximum observability.
- Added safe Edit/View/Details inspection; mutation-grade actions are learned only.
- Added capability promotion from judge-validated seven-phase configuration trajectories.
- Added semantic `plan-future-task` / `run-future-task` with row-scoped rebinding.
- Added three-part portal mutation authorization for future tasks.
- Added FastAPI backend and separate Streamlit frontend.
- Added API Explorer and future-task UI.
- Added FastAPI/Uvicorn requirements and full-stack launchers.
- Added 13 focused platform regressions; complete suite now 541 tests.

## 2026-08-25 — Document Types deep capability learning
- Added `learn-doctypes-deep` and `DocumentTypeDeepDiscoveryFlow`.
- Added Source + Target Document Type listing/search/expand/action learning.
- Added filter option inspection and Next→Previous pagination validation.
- Added safe Edit/Clone/View/History/Audit surface inspection.
- Added network-aborted Migrate/Deploy/Delete prerequisite and payload probes.
- Added unsaved Create Document Type deep execution using the dependency-aware state graph for both input objects.
- Added value-free parent-child dependency blueprints and API evidence for form filling.
- Upgraded HIP Capability Graph to v4 with structural form replay metadata (`input_path`, section, row, dependencies, verification) and no persisted customer values.
- Added `/api/document-types/deep/start`, Streamlit `Deep Learn Document Types`, and run identification.
- Added 10 regressions; complete test inventory is now 558.

## 2026-08-25 — Rules deep capability learning

- Added `RuleDeepDiscoveryFlow` and `learn-rules-deep`.
- Learns Rules listing/search/filter/pagination, row expansion and row actions.
- Safely inspects Edit/Clone/View/Details/History/Audit surfaces without Save/Create/Submit.
- Learns Migrate/Deploy/Delete prerequisites and generated API payload shapes behind a hard network-abort barrier.
- Opens an unsaved Create Rule surface and reuses the production Rules transaction helpers for global form validity, Action Type → async Mapping Identifier, exact Conditions row creation, physical row rebinding and duplicate-name structural recovery.
- Persists value-free Rule dependency/replay topology in HIP Capability Graph v5.
- Added FastAPI `/api/rules/deep/start`, Streamlit `Deep Learn Rules`, run-state visibility and PowerShell launcher.
- Added 10 Rules deep-learning regressions; complete inventory now 568/568 passing.

## 2026-08-25 — Transport Profiles deep capability learning
- Added `TransportProfileDeepDiscoveryFlow` for Source/Target TP listing/actions/create-form learning.
- Hardened TP dependency graph for System Type, Interface Type, Existing Account, Account and folder branches.
- Added exact UI→API request-ID causal trace for TP form filling.
- Added `learn-transport-profiles-deep`, FastAPI endpoint, Streamlit control and PowerShell runner.
- Upgraded persistent HIP Capability Graph to v6.
- Added 12 TP deep-learning regressions; total inventory is 580 tests.

## 2026-08-25 — Full HIP deep-learning consolidation and certification

- Added `FullHIPDeepLearningMission` across Data Maps, Document Types, Rules, Transport Profiles and BizFlows.
- Added crash-resumable family mission state and prior-run family adoption.
- Added fail-closed full input-contract preflight.
- Added `HIPCapabilityCertifier` with operational readiness vs visible-action coverage separation.
- Added global API catalog, replay registry, capability inventory and gap queue artifacts.
- Added CLI `learn-hip-full-deep` and `full-deep-readiness`.
- Added FastAPI `/api/full-deep/start` and `/api/full-deep/readiness`.
- Added Streamlit `Deep Learn ALL HIP + Certify` and `Full HIP Readiness` view.
- Added `RUN_LEARN_HIP_FULL_DEEP.ps1`.
- Regression inventory: 602 tests accounted for.

## 2026-08-25 — Certified Future Task Agent
- Added certification-gated multi-family future-task planning and execution.
- Added deterministic family decomposition and cross-family task replay memory.
- Added guarded adaptive DOM/MCP recovery with AutoGen 0.7.5 ranking over observed candidates only.
- Added mutation ambiguity protection: never auto-retry after mutation click invocation.
- Added fresh Playwright MCP + Chrome DevTools MCP + HIP Intelligence assurance per completed family.
- Added future-task UI→API causal trace and verified cross-family task replay promotion.
- Upgraded HIP Capability Graph to v7 with value-free `task_replay_profiles`.
- Added CLI `plan-certified-task` / `run-certified-task`, FastAPI certified-task endpoints, Streamlit certified task UI, and PowerShell launcher.
- Added 10 certified-task regressions; complete inventory is 612 tests.

## V233 Final / package 2.3.3 - 2026-09-12

- Added final seven-phase mission consolidation gate.
- Added `certify-final-mission` browser-level local UAT command.
- Connected final mission proof to the real `run-full-dummy-fill` completion path.
- Added full lifecycle mock UAT: create all phases, then BizFlow edit/save/validate/deploy.
- Fixed website-understanding semantic labels for actionable text buttons/links/tabs/options when ARIA labels are absent.
- Preserved Stage 1-4 live understanding, Agent Live View, semantic world-model memory, dynamic option resolution and mutation-safe transition planning.
- Bumped package version to 2.3.3.

## V243R5 — Phase-native completion and learn-once exploitation (2026-09-22)
- Made the dedicated HIP phase runtimes authoritative for recognized configuration fill/verify missions.
- Added `NativeHIPPhaseMissionCoordinator` to certify family knowledge, learn only missing families, then execute all selected phases natively.
- Added `phase_vocabulary_learning.py` to learn complete value-free form vocabulary, tabs and dropdown options for Document Type, Rules, Transport Profile and BizFlow create surfaces.
- Prevented generic Universal form filling from superseding the richer native phase runtimes for canonical HIP input.
- Kept the Universal Portal agent as drift/unknown-capability fallback.
- Added fail-closed phase-native qualification before governed mutation execution.
- Added regression coverage for canonical HIP input recognition and family learning readiness.

## V243R7 — 2026-09-22 — Completion-first phase gate and Data Map upload hold

- Required Data Map transformation upload now participates in exact completion proof.
- Explicit Data Map file paths from `input.json` are resolved directly, including relative-to-input paths.
- Added phase-aware stable file-input fallback after DDS/Angular rerender or stale semantic binding.
- Added incomplete-phase Human recovery requests that keep the same browser/session alive.
- Added indefinite hold by default (`incomplete_phase_wait_seconds: 0`) instead of closing Chrome after bounded self-heal stops.
- Human recovery buttons now mean resume/recheck; they do not bypass exact verification.
- Added incomplete-run checkpoint-only terminal path so blocked missions do not emit normal final HTML/CSV/summary ZIP artifacts.
- Added V243R7 completion-first regression tests.
- Full suite: 1,249 passed, 1 skipped, 0 failed.

## V243R8 — Persistent HIP Operator / Open-Ended Goal RSI
- Added `PersistentHIPOperator` for arbitrary search/fill/edit/update/deploy/migrate HIP tasks.
- Added `hip-agent operate-hip` natural-language entry point.
- Added run-local inline patch extraction for explicit `change FIELD to VALUE` requests.
- Universal operator can now borrow an existing BrowserSession so learning/repair cycles keep the same authenticated browser alive.
- Failed cycles are no longer terminal: each cycle updates replay/model/skill/RSI memory and retries the same goal.
- `max_goal_cycles=0` means no attempt-count termination; repeated failure escalates to Human-in-the-Loop while the browser remains open.
- Automated PASS requires final human acceptance by default; human correction returns the same goal to the learning loop.
- `recursive_self_improvement.max_recursive_cycles=0` enables open-ended RSI across persistent goal cycles while keeping each internal policy update auditable.
- Added safe in-place upgrade script; user config, `.env`, `input.json`, runs and memory are preserved.

## V243R9 — 2026-09-23
- Fixed Data Map infinite/repeated autonomous cycles after the form was already correctly filled.
- Runtime-synthesized semantic nodes now count as valid learned controls once exact/authoritative verification passes.
- Human `Looks correct` with stale proof now triggers read-only live reproof instead of being silently converted to rejection.
- Added durable `phase_acceptance_commit.json` and `phase_completion_token.json`.
- Completed phase tokens explicitly prohibit same-phase replay and force next-executable-phase handoff.
- Learning-quality/observability gaps cannot reopen an exact+judge accepted form; they only prevent memory promotion.
