# V243R4 Final Reverification — 2026-09-22

Release identity: **V243R4**  
Python package compatibility version: **2.4.3**

## Reverification summary

- Exact source test discovery: **1,238 tests**
- Passed: **1,237**
- Skipped: **1**
- Failed: **0**
- Python `compileall`: **PASS**
- JavaScript syntax (`node --check`): **PASS**
- `config.yaml` parse: **PASS**
- V243R4 focused closed-loop/production regression: **35 passed, 0 failed**
- Fresh wheel install/import smoke: **PASS**
- Control Center root/runtime/HITL/recipe API smoke: **PASS**
- Source-manifest verification: **PASS**

## Packaging correction made during reverification

The source ZIP previously contained two wheel copies where `release/` had the R4 wheel but `dist/` still contained the earlier R2 wheel. The obsolete `dist/` wheel was replaced with the verified R4 wheel. Both locations now contain the same wheel and the R4 modules:

- `hip_id_agent/trace_self_repair.py`
- `hip_id_agent/human_teaching.py`
- `hip_id_agent/deterministic_recipe.py`
- `hip_id_agent/model_portfolio.py`
- `hip_id_agent/recursive_self_improvement.py`

Canonical wheel SHA-256:

`ce2f6e9153402eb2ec8ef6dbb03f3e99172eaed853d0b45e3a8c2bc442af288a`

## Scope boundary

This certification verifies the packaged source and local/mock/runtime contracts. It does **not** claim that a real Dell tenant was mutated. Dell SSO, tenant-specific DOM changes, live Dell AIA availability, and real Save/Deploy/Migrate operations remain live-environment validations.
