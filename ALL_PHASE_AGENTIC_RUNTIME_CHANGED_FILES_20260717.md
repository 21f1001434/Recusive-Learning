# Changed Files

- `hip_id_agent/phase_runtime_contract.py` — new seven-phase runtime contract registry and validator.
- `hip_id_agent/dummy_fill_e2e.py` — validates all requested phases and runs shared agentic preflight before every attempt.
- `hip_id_agent/runtime_self_heal.py` — adds all-phase attempt preflight using page health, ReAct routing and dual-MCP target verification.
- `tests/test_all_phase_agentic_runtime_contract.py` — coverage and lifecycle regressions.
