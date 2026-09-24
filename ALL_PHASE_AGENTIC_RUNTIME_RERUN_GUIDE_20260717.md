# Rerun Guide

Replace the previous package with the complete ZIP and run the same `run-full-dummy-fill` command. No new CLI flag is required.

Expected root artifact:
- `all_phase_agentic_runtime_contract.json`

Expected per-attempt artifact for every reached phase:
- `<phase>/phase_attempt_01_agentic_preflight.json`

The preflight must show:
- `status: ready`
- `pass: true`
- target route committed
- both MCPs on the expected phase surface
- persistent session retained

A later attempt produces `phase_attempt_02_agentic_preflight.json`, etc., without starting another Chrome session.
