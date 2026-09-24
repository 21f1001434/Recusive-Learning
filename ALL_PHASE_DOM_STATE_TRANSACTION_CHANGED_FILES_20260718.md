# Changed Files — All-Phase DOM State Transactions

## Runtime

- `hip_id_agent/stateful_form_runtime.py`
  - enriches generic control capture with Angular/DDS, geometry and hit-test metadata;
  - adds confidence-bound all-phase control diagnostics;
  - builds one-to-one all-phase form-state models;
  - adds stable transaction waits and committed-field protection;
  - upgrades `execute_phase_state_graph()` to v2 transaction semantics.

- `hip_id_agent/dummy_fill_e2e.py`
  - writes `phase_exact_state_lock.json` after authoritative completion;
  - applies read-only rejudge/no-replay behavior to every completed phase.

- `hip_id_agent/phase_runtime_contract.py`
  - adds the shared transaction and evidence-lock policy to all seven phase contracts.

## Tests

- `tests/test_all_phase_dom_state_transactions.py`
  - all-phase contract coverage;
  - Rule row identity;
  - BizFlow ambiguity rejection;
  - Transport Profile duplicate binding rejection;
  - shared transaction engine and no-replay wiring;
  - phase-agnostic committed-state protection.

## Documentation

- `ALL_PHASE_DOM_STATE_TRANSACTION_FIX_20260718.md`
- `ALL_PHASE_DOM_STATE_TRANSACTION_RERUN_GUIDE_20260718.md`
- `ALL_PHASE_DOM_STATE_TRANSACTION_CHANGED_FILES_20260718.md`
- `LOCAL_TEST_RESULTS_ALL_PHASE_DOM_STATE_TRANSACTION_20260718.txt`
- `CHANGELOG.md`
