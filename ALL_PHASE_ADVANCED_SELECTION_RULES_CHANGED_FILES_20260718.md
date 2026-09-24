# Changed Files — Advanced Selection Rules

- `hip_id_agent/form_interaction_policy.py`
  - Added advanced multi-select, typeahead, radio, checkbox, option loading, upload, tab/accordion, repeatable-row and read-only rules.
  - Added widget contracts and deterministic multi-select exact-set proof.
- `hip_id_agent/dds_control_driver.py`
  - Added stable multi-select option-universe waits and detailed audit.
  - Added exact additive selection and safe explicit extra removal.
  - Added safe checkbox click/check helper.
- `hip_id_agent/stateful_form_runtime.py`
  - Added driver audit and exact-set proof to Document Type and shared phase transactions.
- `hip_id_agent/phase_runtime_contract.py`
  - Applied advanced widget requirements to every phase contract.
- `hip_id_agent/transport_profile_kb.py`
  - Replaced direct radio/checkbox DOM mutation with shared Playwright action contracts.
- `tests/test_advanced_multiselect_and_widget_rules.py`
  - Added policy, proof, executor and all-phase contract regressions.
- `CHANGELOG.md`, `README.md`
  - Added delivery notes.
