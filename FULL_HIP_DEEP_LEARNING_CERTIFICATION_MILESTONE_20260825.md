# Full HIP Deep Learning + Capability Certification Milestone — 2026-08-25

## Goal

Consolidate the five deep HIP portal-family learners into one crash-resumable mission that produces a single, evidence-based readiness verdict for future autonomous tasks.

## Family sequence

1. Data Maps
2. Document Types
3. Rules
4. Transport Profiles
5. BizFlows

The sequence reuses the same persistent Chrome user-data directory. Once Dell SSO is completed, later family learners reuse that authenticated persistent profile while they run sequentially.

## New runtime

`hip_id_agent/full_deep_learning.py`

Provides:

- `FullHIPDeepLearningMission`
- `HIPCapabilityCertifier`
- `latest_certification()`
- crash-resumable family state
- full input-contract preflight
- persistent capability graph merge
- global API catalog
- deterministic replay registry
- capability inventory
- operational readiness verdict
- capability gap queue

## New CLI

```powershell
python -m hip_id_agent.cli learn-hip-full-deep `
  --config .\config.yaml `
  --input-json .\examples\uhaul_poasn_full_dummy_input.json `
  --runs-dir C:\hip_runs `
  --require-mcp `
  --continue-on-family-failure
```

Read latest readiness:

```powershell
python -m hip_id_agent.cli full-deep-readiness --config .\config.yaml
```

## New backend API

- `POST /api/full-deep/start`
- `GET /api/full-deep/readiness`

## New frontend

The separated Streamlit client now includes:

- `Deep Learn ALL HIP + Certify`
- `Full HIP Readiness` tab
- per-family capability/API/replay metrics
- blocker gaps
- coverage warnings
- full-deep run visibility

## Certification model

The certification deliberately separates two concepts:

### Operational readiness

A family is operationally ready only when it has:

- observed page
- Search capability
- row action capability
- form capability for form-based families
- API contract
- observed API response status
- UI-to-API causal evidence
- required verified deterministic replay
- safe discovery proof
- mutation-probe network-abort proof
- value-free persistent memory

### Visible action coverage

The certifier separately reports whether expected row actions were observed:

- Edit
- Clone
- Migrate
- Deploy
- Delete

A permission-dependent missing action becomes a coverage warning and enters the gap queue. It is not silently treated as learned.

## Required verified replay profiles

- Data Maps: at least one verified `entity_*` read/draft replay
- Document Types: `create_source_document_type`, `create_target_document_type`
- Rules: `create_rule`
- Transport Profiles: `create_source_transport_profile`, `create_target_transport_profile`
- BizFlows: `create_biz_flow`

## Generated run artifacts

- `full_deep_learning_mission_state.json`
- `full_deep_input_contract_preflight.json`
- `full_hip_deep_learning_summary.json`
- `hip_capability_certification.json`
- `hip_capability_gap_queue.json`
- `hip_global_api_catalog.json`
- `hip_replay_registry.json`
- `hip_capability_inventory.json`
- existing family-specific deep-discovery evidence

## Resume

A prior full-deep run can be supplied with `--resume-run`. A family is adopted only when its prior deep summary satisfies that family's verified completion criterion. Incomplete families rerun.

## Safety

The consolidation mission preserves all existing deep-learning safeguards. Mutation-grade actions can be structurally learned and safely probed, but discovery does not deliver Deploy/Migrate/Delete or Create/Save/Submit mutations to Dell.

## Test status

602 tests are accounted for:

- 601 normal tests pass
- 1 external MCP-unavailable fail-fast smoke passes under an intentionally unavailable local `npx` stub, matching the test's intended offline behavior
