# V243R13 — All-phase completion + learning/RSI unblock (2026-09-24)

## Symptom (run `UHAUL-POASN-20260924-103934`)

The mission stalled at **P01 Data Map**:

```
Data Map autonomous goal was not proven: {"reason": "goal not proven before bounded
adaptive/no-progress guard", "cycles": [... 5 × "retry_required"], "failed_attempts": []}
Automated: BLOCKED • deterministic: not proven • exact checkpoint: not proven
```

The Create Map drawer was filled correctly and showed `Map identifier already exists`, the
same message as the approved golden screenshot. `Looks correct` could not unblock the phase,
and Start teaching / Finish & learn did not learn anything.

## Root causes, reproduced with the real executors in headless Chromium

### Blocks every phase and every user task

| # | Cause | Effect |
|---|-------|--------|
| 1 | `security.SECRET_KEY_RE` contained a bare `auth`, which matches `authoritative_…`. `mask_sensitive_data()` replaced `authoritative_execution_verified: True` with `"***MASKED***"`. | Every strict `… is True` gate failed:<br>• the autonomous runtime returned `retry_required` on every cycle, for all 7 phases;<br>• `phase_exact_completion_checkpoint` reported *exact checkpoint: not proven*, so human approval could not help;<br>• the task box (`/api/portal-task/run`) filled forms correctly and still reported `incomplete`. |
| 2 | `safe_write_json()` masks by default, and the masker treated agent-owned keys as secrets: `session_id`, `sessions`, `task_tokens`. | Learning never persisted usefully:<br>• **Interactive teaching:** sessions were saved as `session_id: ***MASKED***`, so Finish & learn looked for `***MASKED***.json` and silently captured nothing.<br>• **Learned recipes, induced skills and replay policies:** reloaded with `task_tokens == "***MASKED***"`, so they never matched a new or differently worded task. |
| 3 | The autonomous failure result had no execution attached. | Every blocker showed `failed_attempts: []`. |

### Phase-specific (every golden screenshot shows these states)

| Phase | Cause | Effect |
|-------|-------|--------|
| Data Map | *Map Identifier Version* is disabled and can show `1` only as a placeholder. | The verify-only node failed forever. |
| Source/Target Document Type | The Document Type executor treated `Name already exists` as `HIP_FIELD_VALIDATION_BLOCKING` and had no reconciliation pass. | These phases could **never** complete. |
| Source/Target Document Type | Status is a DDS **switch**, but the Document Type scanner mapped Status only for radios. It also compared and operated a switch as a radio. | The Status node was skipped, so exact proof was impossible. |
| Rule | Version, Rule Type and Rule Scope are disabled portal-owned fields. The executor refused disabled controls and compared only their empty value. | The phase could not be proven. |
| Doc Type / Transport Profile / BizFlow | The validation gate only recognised the Data Map and Rule duplicate messages. | `Name already exists` and `Transport Profile already exists in DEV environment.` blocked phase verification. |
| All | `requirements.txt` pinned `websockets==13.1`, but `browser-use==0.13.8` requires `15.0.1`. | A clean `pip install -r requirements.txt` failed. |

### Learning and recursive self-improvement wiring

- **Start mission never ran RSI.** `recursive_self_improvement` was called only by the task box, production E2E and the persistent operator, so full phase missions never ran:
  - model-champion dreaming over the judge-panel and AutoWebGLM outcomes they recorded;
  - bounded RSI cycles;
  - skill-library review.

  The mission now runs the same RSI cycle after its replay episode. The new `mission_learning.close_mission_learning_loop()` writes `recursive_self_improvement.json` in the run folder and updates `data/hip_memory/recursive_self_improvement/`.
- Every learning store was round-trip verified: learn → write to disk → reload → exploit. The stores are continuous learning, capability graph, Portal Brain, trajectory memory, replay policy (including dreaming), model portfolio champions, RSI state, induced skills, deterministic recipes, flow-pattern fast replay, human phase review, human teaching, interactive teaching and the website world model. After the masking fixes, none of their files contain `***MASKED***`.

## Fixes

- `security.py`:
  - `auth` is now `auth(?!or)`;
  - boolean and `None` values are never masked;
  - explicit `NON_SECRET_KEYS` for agent-owned keys (`task_tokens`, `session_id`, `sessions`, …).
  - Real credentials are still masked: `authorization`, `auth`, `*_token`, `cookie`, `password`, `secret`, …
- `form_interaction_policy.inspect_interaction_state()` now returns the field's own `validationMessage`.
- `stateful_form_runtime.py`, for both the generic and the Document Type executors:
  - The natural-key duplicate message is recorded as `existing_object_validation`, not as a failure. Natural keys are `map_identifier`, `document_type_name`, `rule_name`, `profile_name` and `business_flow_name`. Every other validation error still blocks.
  - A disabled or read-only portal-owned control counts as verified when its displayed value (value or placeholder) matches `input.json`. A mismatch reports `HIP_READONLY_PORTAL_VALUE_MISMATCH` (fix `input.json` or the portal object) instead of looping.
  - The Document Type Status switch is recognised, operated with `set_boolean_control` and compared as a boolean.
- `autonomous_form_runtime.py`:
  - each failed cycle records `unmet_success_checks`;
  - failures carry `last_cycle_execution` and `failure_summary`;
  - the new `autonomous_target_execution()` is used by all five phase KB modules, so blockers name the failing field and check.
- `dummy_fill_e2e.py`: the portal's natural-key duplicate message is existing-object evidence for all 7 phases. `Attribute Name already exists` (a row-level input error) and conflicting inventory objects still block.
- `requirements.txt` / `pyproject.toml`: `websockets==15.0.1`.

## Evidence

On the original V243R12H2 code:
- every phase replica fails, because the proof flag reads `***MASKED***`;
- the learning round-trip tests fail 5 of 5;
- the task-box fill test fails.

With only the masking fix, the Document Type and Rule replicas still fail.

The new tests:

| Test file | What it proves |
|-----------|----------------|
| `test_v243r13_datamap_stuck_learning_fix.py` | Data Map Create Map replica: goal proven on cycle 1, with both version renderings. |
| `test_v243r13_all_phase_existing_object_replicas.py` | Document Type, Rule, Transport Profile and BizFlow replicas with their golden duplicate messages and disabled fields: goal proven on cycle 1. The validation gate accepts each golden message. |
| `test_v243r13_universal_task_fill_and_learn.py` | A task-box form fill reaches `100_percent_runtime_input_exact_readback`, and the learned blueprint is value-free. |
| `test_v243r13_learning_loop_end_to_end.py` | Continuous learning promotes trusted knowledge and replay exploits it after reload. Mission RSI elects model champions and persists its cycles. Flow-pattern memory learned on run 1 drives run 2. |
| `test_v243r13_learning_memory_roundtrip.py` | Finish & learn captures from the saved session. Recipes, induced skills and replay policies still match a re-worded task after reload. |

## Apply

```powershell
.\APPLY_V243R13_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R13_INSTALL.ps1
```

The script preserves `config.yaml`, `.env`, `input.json`, `runs`, `data\hip_memory`, `.backend_runtime` and `.hip_runtime`. After applying:
1. Restart the Control Center.
2. Start a **new** mission. Old run folders and memory files contain values that were already masked, so they cannot be re-proven.
3. Confirm each phase once with **Looks correct**. That promotes the phase to deterministic replay for later runs.

Validation boundary: verified against source, the full local test suite and browser replicas
built from the golden screenshots. It was not run against a live Dell HIP tenant. Live SSO,
DDS dropdown option lists, JAR upload, the text/vision judges and BizFlow wizard navigation
remain live checks. If a phase still blocks, the error now names the field and the unmet check.
