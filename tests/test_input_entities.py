import json
from pathlib import Path

from hip_id_agent.input_entities import extract_input_entities


def test_extract_entities_from_nested_sender_receiver(tmp_path: Path):
    p = tmp_path / "input.json"
    p.write_text(json.dumps({"sender": {"partnerName": "UHAL", "systemName": "UHAL-SFTP"}, "receiver": {"partnerName": "DELL", "systemName": "DELL-HIP"}}), encoding="utf-8")
    out = extract_input_entities(p)
    assert out["source_partner"] == ["UHAL"]
    assert out["source_system"] == ["UHAL-SFTP"]
    assert "DELL" in out["target_partner"]
