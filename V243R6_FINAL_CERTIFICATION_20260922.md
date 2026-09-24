# V243R6 Final Source Certification — 2026-09-22

Release: **HIP Portal V243R6 — Judge Reconciliation + Learning HITL**  
Compatibility package version: **2.4.3**

## Verified source behavior

- Full pytest collection: **1,245 tests**
- Accounted result: **1,244 passed, 1 skipped, 0 failed**
- Focused R6 + judge/native/production regression: **110 passed, 0 failed**
- Python compileall: **PASS**
- Backend WebUI JavaScript syntax: **PASS**
- Fallback WebUI JavaScript syntax: **PASS**

## R6 capabilities verified

- exact-evidence-first judge reconciliation
- bounded on-prem multi-model judge consensus on disagreement
- model panel cannot override failed exact browser evidence
- one human phase-review checkpoint per newly learned run+phase
- review can confirm an exact-proven model-only false negative
- review can reject an automated PASS and force supervised repair
- resolved review can update judge-model downstream reward
- Control Center APIs/UI for `Looks correct` and `Needs correction`
- native HIP phase routing and R5 deep-learning/exploitation behavior retained
- R4 trace repair, HITL field teaching, deterministic recipes, replay/dreaming and RSI retained

## Installable wheel

Both `dist/` and `release/` contain the same R6 wheel.

SHA-256:

```text
e318d087057696deb84069022bc48d815a0880baf2c974c09ec109b5dc2f8d9c
```

The clean-installed wheel was smoke-tested for R6 imports and FastAPI endpoints.

## Validation boundary

Local/browser-mock and package certification do not claim a real Dell tenant mutation. Dell SSO, current live HIP DOM/DDS behavior, tenant permissions, Dell AIA model availability, and Save/Create/Edit/Deploy/Migrate outcomes remain live-environment validation items.
