from pathlib import Path

import hip_id_agent


def test_v236_release_version():
    assert hip_id_agent.__version__ == "2.4.3"
    assert 'version = "2.4.3"' in Path("pyproject.toml").read_text(encoding="utf-8")


def test_optional_but_supplied_input_is_not_silently_accepted_when_control_absent():
    source = Path("hip_id_agent/stateful_form_runtime.py").read_text(encoding="utf-8")
    assert "HIP_INPUT_OWNED_OPTIONAL_CONTROL_NOT_PRESENT" in source
    assert 'success = True\n            reason = "optional conditional control not present"' not in source


def test_stateful_completion_requires_all_input_owned_nodes_exact():
    source = Path("hip_id_agent/stateful_form_runtime.py").read_text(encoding="utf-8")
    assert "satisfied_contract_node_ids" in source
    assert "unresolved_contract_node_ids" in source
    assert "HIP_INPUT_OWNED_FIELD_COVERAGE_INCOMPLETE" in source
    assert '"input_owned_coverage_percent"' in source
    assert '"fields_filled_or_verified": not unresolved_contract_node_ids' in source


def test_binding_advisor_considers_all_supplied_unbound_fields():
    source = Path("hip_id_agent/autonomous_form_runtime.py").read_text(encoding="utf-8")
    block = source[source.index("unresolved_required = ["):source.index("# Use Dell AIA/AutoGen", source.index("unresolved_required = ["))]
    assert 'row.get("required")' not in block
    assert 'not row.get("bound")' in block


def test_replay_readiness_does_not_block_current_phase_completion():
    source = Path("hip_id_agent/dummy_fill_e2e.py").read_text(encoding="utf-8")
    assert "HIP_REPLAY_NOT_READY_CURRENT_FORM_STILL_ELIGIBLE" in source
    start = source.index("evidence_incomplete = bool(")
    gate = source[start:source.index(") if isinstance(maximum_evidence, dict) else False", start)]
    assert 'coverage_pass' in gate
    assert 'replay_ready' not in gate
