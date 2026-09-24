from pathlib import Path

from hip_id_agent.dummy_fill_e2e import PHASE_SEQUENCE
from hip_id_agent.phase_runtime_contract import PHASE_RUNTIME_CONTRACTS, validate_phase_runtime_contracts


def test_all_seven_phases_have_complete_agentic_contracts():
    result = validate_phase_runtime_contracts(PHASE_SEQUENCE)
    assert result["pass"] is True
    assert result["covered_phases"] == PHASE_SEQUENCE
    assert set(PHASE_RUNTIME_CONTRACTS) == set(PHASE_SEQUENCE)
    for phase in PHASE_SEQUENCE:
        contract = PHASE_RUNTIME_CONTRACTS[phase]
        assert contract["url"]
        assert contract["surface_markers"]
        assert contract["exact_state_checks"]
        assert contract["input_object"]


def test_every_phase_attempt_uses_shared_agentic_preflight():
    source = Path(__file__).parents[1].joinpath("hip_id_agent", "dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "contract = PHASE_RUNTIME_CONTRACTS[phase]" in source
    assert "await runtime_self_healer.prepare_phase_attempt(" in source
    assert source.index("await runtime_self_healer.prepare_phase_attempt(") < source.index("summary = await _execute_phase_once")


def test_all_phase_lifecycle_contains_react_mcp_judge_self_heal():
    result = validate_phase_runtime_contracts(PHASE_SEQUENCE)
    lifecycle = set(result["lifecycle"])
    assert "kb_guided_react_route" in lifecycle
    assert "dual_mcp_same_surface" in lifecycle
    assert "exact_state_verification" in lifecycle
    assert "text_and_vision_judge" in lifecycle
    assert "bounded_self_heal" in lifecycle
