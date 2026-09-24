# Changed Files

## Added

- `hip_id_agent/form_interaction_policy.py`
  - shared mandatory interaction rules
  - structural-parent reveal
  - animation/bounding-box stability
  - hit-test and validation inspection
  - explicit event proof
  - conditional child eligibility
  - learning vs validated fast replay profile

- `tests/test_all_phase_form_interaction_policy.py`
  - policy coverage
  - fast replay eligibility
  - explicit event proof
  - all-phase contract inheritance
  - shared executor integration
  - DDS click/check contract

## Modified

- `hip_id_agent/stateful_form_runtime.py`
  - shared pre-action preparation
  - hidden parent reveal and parent recommit
  - conditional child visibility gate
  - adaptive stability intervals
  - explicit event and validation proof
  - fast replay profile and blueprint output
  - applied to Document Type and all other phase families

- `hip_id_agent/phase_runtime_contract.py`
  - all seven phases now require structural-parent-first, explicit widget events, child visibility, animation stability, hit-test, stale-node rebind, validation gate and learn-once fast replay

- `hip_id_agent/dummy_fill_e2e.py`
  - writes run-level `all_phase_form_interaction_policy.json`

- `CHANGELOG.md`
  - records the all-phase interaction policy and fast replay upgrade
