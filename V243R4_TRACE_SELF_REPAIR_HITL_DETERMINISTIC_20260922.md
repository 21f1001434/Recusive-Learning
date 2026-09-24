# HIP Portal V243R4 — Trace Self-Repair, Multi-Model Learning, Deterministic Replay & Human Teaching

## Goal
V243R4 closes the loop between runtime evidence, Dell on-prem LLM diagnosis, policy learning, human correction, and fast deterministic execution. It preserves the V243 production safety contract: customer-entered values, transient selectors and screen coordinates are not promoted into long-term memory, and learned behavior never authorizes Save/Create/Edit/Deploy/Migrate by itself.

## End-to-end execution lifecycle

1. **Plan from the current task + input.json.** The Universal Portal planner first checks for a validated deterministic recipe, then an induced skill/replay policy, and finally adaptive exploration.
2. **Observe the live surface.** DOM, accessibility, Angular/DDS state, MCP evidence, current-generation semantic control IDs, screenshot/vision evidence and recent event/mutation evidence are used to bind fields.
3. **Fill and prove exact coverage.** Every required input path is mapped to a live control and read back. Repeatable rows are expanded from the input structure rather than from hard-coded row counts.
4. **If the run stalls/fails, write a bounded trace.** `universal_portal_task_execution.partial.json` plus recent masked mission/recovery evidence is collected by `TraceSelfRepairEngine`.
5. **Multi-model trace diagnosis.** The existing `OnPremModelPortfolioRouter` runs the recovery task across the configured Dell on-prem text-model portfolio. Candidate diagnoses are bounded to approved recovery strategies and are scored through the existing champion/challenger/downstream-reward machinery.
6. **Automatic policy repair when safe.** A high-confidence non-mutating diagnosis may trigger adaptive re-discovery/rebinding/refresh/dropdown retry/repeatable-row repair. It does not edit Python source, invent unproved selectors, or grant mutation approval.
7. **Human assistance when evidence is ambiguous.** The run creates a value-free assistance request containing unresolved `input.json` paths and the currently observed semantic controls. In the Control Center, the operator can select an unresolved input path and click a candidate control in Agent Live View, then choose **Teach mapping**.
8. **Human teaching becomes semantic memory.** Only `input_path -> semantic_control_id/label/section/role` is retained. No customer value, CSS/XPath selector, screen coordinate, token or password is stored. On the next/resumed attempt, the taught mapping is still re-bound to the current live DOM and exact readback is required.
9. **Reward verified outcomes.** Replay policy, model portfolio, skill confidence and bounded RSI receive the final verified reward. Failed advice is penalized; a model does not become champion merely because its text sounded plausible.
10. **Compile repeated success into a deterministic recipe.** After the configured number of verified high-reward successes (default 2, average reward >= 0.90), `DeterministicRecipeLibrary` promotes the semantic workflow to `validated`.
11. **Fast exploitation on later runs.** A matching validated recipe is executed before adaptive discovery. It behaves like a script in ordering and semantic intent, while still resolving current-generation controls and proving each action on the live page.
12. **Automatic drift fallback.** If the portal changes and the recipe no longer proves itself, it is penalized/demoted and execution falls back to adaptive exploration + trace repair rather than repeatedly replaying a stale script.

## Human-in-the-loop workflow

The Control Center now contains **Human Assistance / Teach Agent**.

- `Refresh assistance` loads unresolved input paths for the active/latest run.
- Agent Live View candidate labels are clickable.
- Clicking a candidate copies its semantic control identity into the teaching panel.
- Select the unresolved input path that belongs to that control.
- Optional note can explain the relationship without storing the actual field value.
- `Teach mapping` persists a reusable semantic binding.
- Rerun/resume the phase. The runtime rebinds that semantic identity to the current DOM and validates exact readback.

API endpoints:

- `GET /api/human-assistance?config=config.yaml&run_id=<optional>`
- `POST /api/human-assistance/teach`
- `GET /api/deterministic-recipes?config=config.yaml`

## Trace self-repair strategies

Only the following bounded strategies can be emitted by the trace repair engine:

- `adaptive_rediscovery`
- `rebind_controls`
- `refresh_surface`
- `retry_dropdown_live`
- `expand_repeatable_rows`
- `human_teach`
- `stop_and_report`

Low-confidence diagnoses are automatically converted into a human-assistance requirement.

## Multi-model policy

The shipped config keeps the Dell on-prem portfolio and runs up to three recovery models in parallel (bounded by `max_parallel_models`). The router stores task/role performance and selects a champion only after downstream verified outcomes. Fast exploitation may use the current champion; low-confidence/drift cases can re-open the tournament.

Configured text models include `gpt-oss-120b`, `gpt-oss-20b`, `mistral-small-3-1-24b-instruct-2503`, `llama-3-3-70b-instruct`, `gemma-3-27b-it`, and `llama-3-2-3b-instruct`, subject to actual Dell AIA availability.

## Deterministic recipe safety

A promoted recipe stores ordering and semantic intent only. It deliberately does **not** store:

- customer-entered values;
- secrets or credentials;
- CSS/XPath selectors;
- transient DDS option tokens;
- PyAutoGUI coordinates.

Every replay requires live reproof. Mutation governance and approval remain separate from learning.

## R3 live-failure fixes incorporated

V243R4 also carries the runtime fixes identified from the September 21 failed run:

- PyAutoGUI detects unreliable primary-screen geometry/secondary-monitor coordinates and yields to browser execution rather than looping on invalid coordinates.
- Ephemeral DDS option-token selectors are not sent through the Playwright-MCP selector parser.
- Completion-first self-heal cannot advance from a blocked phase; the current phase must pass before the next phase starts.
- Exact requested HIP routes can use an interactive Angular structural readiness proof when heading text is late; exact form/judge verification remains mandatory.
- Runtime evidence traversal tolerates disappearing OneDrive directories instead of crashing on `FileNotFoundError`/`WinError 3`.

## Recommended Windows runtime layout

Keep the source wherever required, but place high-churn runtime evidence outside OneDrive when possible, for example:

```powershell
$env:HIP_RUNS_DIR = "C:\hip_runs"
```

Then start the Control Center and production doctor before a live run.

```powershell
.\START_HIP_PORTAL.ps1
.\PRODUCTION_DOCTOR.ps1
.\RUN_PRODUCTION_E2E.ps1
```

## Important live-environment boundary

The automated certification validates source, tests, local/mock Chromium flows and package behavior. Dell SSO, current tenant DOM, live Dell AIA model availability, and real Save/Create/Edit/Deploy/Migrate effects must still be proven in the authorized Dell environment. The learning layer does not weaken that requirement.
