# V243R13 — Data Map learning phase unblock (2026-09-24)

## Symptom (run `UHAUL-POASN-20260924-103934`)

Control Center → Learning-phase review → **Review Data Map**:

```
Data Map autonomous goal was not proven: {"reason": "goal not proven before bounded
adaptive/no-progress guard", "cycles": [{"cycle": 1, "status": "retry_required"}, ...
{"cycle": 5, "status": "retry_required"}], "failed_attempts": []}
Automated: BLOCKED • deterministic: not proven • exact checkpoint: not proven
```

The Create Map drawer was filled correctly (identifier, name, class, Contivo 6.7,
JAR uploaded) and showed `Map identifier already exists`, the same message as the
human-approved golden screenshot `golden_screenshots/UHAUL-POASN/Data Map.png`.
`Looks correct` could not unblock the phase because a human approval requires exact
browser proof, and that proof was never produced.

## Root causes (reproduced with the real executor + headless Chromium)

| # | Cause | Effect |
|---|-------|--------|
| 1 | `security.SECRET_KEY_RE` contained a bare `auth`, which matches `authoritative_…`. `mask_sensitive_data()` replaced `authoritative_execution_verified: True` with `"***MASKED***"`. | Every strict `… is True` gate failed: the autonomous runtime returned `retry_required` on every cycle, and `phase_exact_completion_checkpoint` reported *exact checkpoint: not proven*. This affected every phase that runs in strict mode, not only Data Map. |
| 2 | *Map Identifier Version* is a disabled, portal-owned input. When it shows `1` only as a placeholder, the verify-only node read an empty value and failed on every cycle. | Adaptive cycles failed identically, with the misleading reason "did not commit exact stable value after repair". |
| 3 | The autonomous failure result had no execution attached. Phase modules read `final_execution or {}`. | Every blocker showed `failed_attempts: []`, which hid the real cause. |
| 4 | The downstream validation gate accepted `Map identifier already exists` only when the read-only listing inventory also found the map. | If the inventory had not paged to the map, the approved "already exists" state still blocked the phase. |
| 5 | `requirements.txt` pinned `websockets==13.1`, but `browser-use==0.13.8` requires `websockets==15.0.1`. | A clean `pip install -r requirements.txt` failed with `ResolutionImpossible`. |

## Fixes

- `hip_id_agent/security.py`: `auth` is now `auth(?!or)`. `auth`, `x-auth-token`, `oauth…` and `authorization` are still masked, but `authoritative_*` is not. Boolean and `None` values are never masked, because they carry no secret material.
- `hip_id_agent/stateful_form_runtime.py`: a verify-only node on a disabled or read-only control with an empty value accepts the portal-displayed placeholder. A real mismatch on a read-only control now reports `HIP_READONLY_PORTAL_VALUE_MISMATCH` (fix `input.json` or the portal object) and does not retry a "repair".
- `hip_id_agent/autonomous_form_runtime.py`:
  - each failed cycle records `unmet_success_checks`;
  - failures include `last_cycle_execution` and a `failure_summary`;
  - new `autonomous_target_execution()` returns the proven execution, or a `pass: False` stub that carries the real failed attempts.
- Data Map, Document Type, Rules, Transport Profile and BizFlow KB modules use `autonomous_target_execution()`, so blocker messages name the failing field and check.
- `hip_id_agent/dummy_fill_e2e.py`: a portal-reported `… already exists` message is accepted as `existing_object_reported_by_portal` in no-save runs. An inventory row with a *different* name or class (`conflicting_existing_object`) still blocks.
- `requirements.txt` / `pyproject.toml`: `websockets==15.0.1`.

## Evidence

`tests/test_v243r13_datamap_stuck_learning_fix.py` drives the real
`execute_autonomous_phase_goal()` in headless Chromium against
`tests/fixtures/create_map_duplicate_identifier.html`. That replica of the Create Map
drawer includes the asynchronous duplicate-identifier validator and the disabled version field.

- Before the fix: `retry_required` on every cycle, and `authoritative_execution_verified == "***MASKED***"`.
- After the fix: `goal_achieved` on cycle 1 for both version renderings (value and placeholder), with the duplicate warning still visible.
- Genuine mismatch: the failure names `map_identifier_version` / `HIP_READONLY_PORTAL_VALUE_MISMATCH` and each unmet check.

## Apply

```powershell
# From the extracted V243R13 package folder, targeting your existing install:
.\APPLY_V243R13_IN_PLACE.ps1 -TargetRoot C:\path\to\your\HIP_PORTAL
.\VERIFY_V243R13_INSTALL.ps1
```

`config.yaml`, `.env`, `input.json`, `runs`, `data\hip_memory`, `.backend_runtime`
and `.hip_runtime` are preserved. Then restart the Control Center
(`RUN_JAVASCRIPT_UI.ps1` / `START_HIP_PORTAL.ps1`) and click **Refresh phase review**,
or start a new mission. Earlier run folders contain masked JSON from the old code, so
re-run the Data Map phase instead of re-judging an old run.

Validation boundary: this was verified against source, the full local test suite and a
browser replica of the Create Map drawer. It was not run against your live Dell HIP
tenant. Live SSO, the DDS Contivo dropdown and the JAR upload remain live checks.
