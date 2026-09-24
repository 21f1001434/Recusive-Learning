# All-Phase Agentic Runtime Coverage Fix — 2026-07-17

## Scope
The shared persistent-session, KB-guided ReAct routing, autonomous page-health, dual-MCP verification, exact-state verification, text/vision judging, and bounded self-heal lifecycle is now enforced for all seven HIP phases:

1. Data Map
2. Source Document Type
3. Target Document Type
4. Rule
5. Source Transport Profile
6. Target Transport Profile
7. BizFlow

## Implementation
- Added `hip_id_agent/phase_runtime_contract.py` with an explicit runtime contract for every phase.
- Added startup validation; execution fails before opening the portal if any requested phase lacks a complete contract.
- Added `all_phase_agentic_runtime_contract.json` to every run.
- Added `RuntimeSelfHealController.prepare_phase_attempt(...)` before every phase attempt.
- Each attempt now enforces active-page recovery, tab consolidation, DOM-observer readiness, autonomous page-health, KB/ReAct target routing, and strict dual-MCP target agreement.
- Existing exact verification, text/vision judge, self-heal replay, and candidate-memory promotion continue after phase execution.

## Safety
No final Save/Create/Submit/Delete/Deploy/Publish action was enabled. Repairs remain bounded and fail-closed.

## Verification
- Targeted tests: 19 passed
- Full suite: 338 passed
