# Changed files

- `hip_id_agent/browser_session.py`
  - strict requested-module usability contract;
  - executor-agreement versus target-commit separation;
  - bounded navigation ReAct controller;
  - plan/act/observe/judge evidence;
  - route, MCP-drift, and SSO recovery.
- `hip_id_agent/dummy_fill_e2e.py`
  - imports `mask_sensitive_string`;
  - classifies route/MCP/auth failures;
  - retries recoverable navigation in the same persistent context.
- `tests/test_agentic_react_navigation_controller.py`
  - supplied-run regression coverage.
