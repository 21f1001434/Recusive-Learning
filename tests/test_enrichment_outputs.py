import json
from pathlib import Path

from hip_id_agent.api_client import HipAPIClient
from hip_id_agent.config import APIConfig


def test_enriched_input_real_and_redacted(tmp_path: Path):
    src = tmp_path / "input.json"
    src.write_text(json.dumps({"partnerId": "${partner_id}", "systemId": "${system_id}", "password": "real-secret"}), encoding="utf-8")
    out = tmp_path / "enriched_input.json"
    info = HipAPIClient(APIConfig()).save_enriched_input(src, out, partner_id="10483", system_id="SYS123")
    real = json.loads(out.read_text(encoding="utf-8"))
    red = json.loads(Path(info["redacted_output"]).read_text(encoding="utf-8"))
    assert real["partnerId"] == "10483"
    assert real["systemId"] == "SYS123"
    assert real["password"] == "real-secret"
    assert red["password"] == "***MASKED***"
    assert info["execution_file_unmasked"] is True
    assert info["report_file_redacted"] is True
