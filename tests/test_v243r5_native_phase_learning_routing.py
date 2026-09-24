from pathlib import Path
import json

from hip_id_agent.native_hip_phase_mission import NativeHIPPhaseMissionCoordinator
from hip_id_agent.capability_graph import HIPCapabilityGraph


def test_native_recognizes_canonical_hip_input(tmp_path: Path):
    payload = {
        "objects": {
            "data_map": {"name": "DM", "mapType": "Contivo", "contivoVersion": "1"},
            "source_document_type": {"name": "SRC", "version": "1"},
            "target_document_type": {"name": "TGT", "version": "1"},
            "rule": {"name": "RULE"},
            "source_transport_profile": {"name": "STP", "interfaceType": "SFTP"},
            "target_transport_profile": {"name": "TTP", "interfaceType": "SFTP"},
            "biz_flow": {"name": "BF"},
        }
    }
    p = tmp_path / "input.json"; p.write_text(json.dumps(payload))
    row = NativeHIPPhaseMissionCoordinator.recognize(p)
    assert row["recognized"] is True
    assert "data_map" in row["selected_phases"]
    assert "rule" in row["selected_phases"]
    assert "biz_flow" in row["selected_phases"]


def test_learning_readiness_is_fail_closed_until_replays_exist(tmp_path: Path):
    graph = HIPCapabilityGraph(tmp_path / "brain")
    class Cfg: pass
    coord = NativeHIPPhaseMissionCoordinator(Cfg(), graph)
    ready = coord.learning_readiness(["document_types", "rules", "transport_profiles", "bizflows"])
    assert ready["ready"] is False
    assert set(ready["missing_families"]) == {"document_types", "rules", "transport_profiles", "bizflows"}
