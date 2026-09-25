"""V243R19: certified replay on the full phase replicas, and the provenance fixes it needed.

The goal engine's first pass must prove every field on its own; a value set
through a radio group's option or a label click is credited to the control the
executor bound.  Otherwise the pass ends with
``HIP_PHASE_AUTHORITATIVE_INTERACTION_NOT_VERIFIED`` and all of its work is
repeated by a second pass -- and a certified replay could never be a single pass.
"""
from __future__ import annotations

from pathlib import Path

from phase_replica_support import dom_values, run_variant_replica


def test_a_button_radio_group_choice_has_provenance_so_the_first_pass_holds(tmp_path: Path):
    result, _ = run_variant_replica(tmp_path, "rule", broker=True, max_cycles=1, observers=True)
    assert result["pass"] is True, result.get("failure_summary")
    first_pass = result["cycles"][0]["non_file_execution"]
    assert first_pass.get("pass") is True, first_pass.get("error")


def test_document_type_is_learned_then_replayed_in_a_single_certified_pass(tmp_path: Path):
    learn, _ = run_variant_replica(tmp_path, "source_document_type", broker=True, observers=True)
    assert learn["pass"] is True, learn.get("failure_summary")
    assert learn["skill"]["outcome"]["status"] == "candidate"
    replay, dom = run_variant_replica(tmp_path, "source_document_type", broker=True, observers=True)
    assert replay["pass"] is True, replay.get("failure_summary")
    assert replay["execution_mode"] == "deterministic_replay"
    assert replay["skill"]["outcome"]["status"] == "certified"
    assert len(replay["cycles"]) == 1
    cycle = replay["cycles"][0]
    assert cycle["replay_state_check"]["holds"] is True  # one pass + one read-back
    assert cycle["stage_seconds"]["verify_pass"] < 1.0
    assert "Value" in {d["key"] for d in dom} and "ISA" in dom_values(dom, "Value")
