# v2.2.3 — Chrome-first + UTF-8 + Semantic Judge + Phase Trace Hardening

Date: 2026-09-09

## Purpose

This release addresses the live Windows blockers observed in the September 9 HIP mission screenshots while preserving the v2.2.2 uncapped Dell AIA text/vision response-quality changes.

## Implemented changes

### Chrome-first persistent browser

- Google Chrome Stable is the default configured browser (`portal.chromium_channel: chrome`).
- Startup fallback order is Chrome -> Microsoft Edge -> Playwright Chromium.
- Chrome and Edge use separate persistent user-data directories.
- Fallback is startup-only. After Dell SSO/session ownership is established, the mission does not switch browsers underneath an authenticated run.
- Live readiness uses the same browser launch order as the mission runtime.
- An explicit `msedge` override remains supported for environments that require Edge.

### Windows UTF-8 execution safety

The runtime now force-overrides inherited Windows console settings at every supported process boundary:

- `PYTHONUTF8=1`
- `PYTHONIOENCODING=utf-8`
- `PYTHONLEGACYWINDOWSSTDIO=0`

Python stdout/stderr are reconfigured to UTF-8 with replacement-safe error handling. The policy is applied in FastAPI, mission subprocesses, Streamlit/legacy entry points, Bun-launched backend processes, and Windows PowerShell launchers. Unicode portal/model text such as U+2011 non-breaking hyphen therefore cannot terminate a mission with a cp1252 `UnicodeEncodeError`.

### Semantic section-judge evidence

- Every governed HIP phase compiles judge expectations from `compile_phase_state_graph()`.
- Facts carry semantic field identity, section, exact expected value, input path, repeatable-row kind, and repeatable-row index.
- Generic flattened `input_value[N]` expectations are prohibited for Data Map, Document Type, Rule, Transport Profile, and BizFlow.
- Document Type keeps its stable historical row-scoped judge IDs (`attributes[0].expression`, etc.) as semantic aliases/identities while the state graph remains the authoritative source.
- Every non-empty live form control is eligible as post-fill evidence, including enabled/writable/non-required fields.
- Deterministic matching remains field/row scoped: the same value in an adjacent field or repeatable row cannot satisfy the expected field.

### Correct phase-handoff trace ownership

- Source-phase cleanup stays attributed to the source phase.
- Destination navigation during `handoff_to_next_phase()` is attributed to the destination phase.
- The destination receives an explicit verified-route handoff observation after navigation/surface verification.
- This prevents a Transport Profiles route, for example, from being displayed as though it were the final Target Document Type action.

### Better blocked-phase diagnostics

Mission trace refresh now records concise deterministic judge diagnostics including:

- missing semantic fields,
- expected values (masked),
- row issue count,
- failed attempt count,
- text-judge status/pass,
- vision-judge status/pass.

The JavaScript phase card renders the first useful missing-field/judge summary instead of only a generic BLOCKED badge.

## Regression coverage

The v2.2.3 blocker-hardening regression suite covers Chrome-first config and fallback, Chrome CDP health fallback, explicit Edge override, UTF-8 override/Unicode round trip, semantic state-graph expectations for all seven mission phases, repeatable-row identity, wrong-adjacent-field rejection, post-fill normal-control evidence, phase trace override/handoff attribution, diagnostics, and release version.

Full source-tree regression at promotion: **1028 / 1028 PASS**.
