from __future__ import annotations

import json
from pathlib import Path

from hip_id_agent.form_api_agent import build_api_transactions, build_payload_response_index
from hip_id_agent.streamlit_dashboard import (
    PHASES,
    build_evidence_archive,
    build_mission_command,
    collect_api_transactions,
    collect_crosswalk_rows,
    validate_json_upload,
)


def test_api_transaction_ledger_captures_redacted_request_and_response() -> None:
    rows = build_api_transactions([
        {
            "request_id": "req-1",
            "url": "https://hip.example/api/rules/validate?account=123",
            "method": "POST",
            "status": 200,
            "resource_type": "xhr",
            "stage": "rule_fill",
            "request_headers": {"Authorization": "Bearer secret-token", "Content-Type": "application/json"},
            "response_headers": {"Set-Cookie": "session=secret", "Content-Type": "application/json"},
            "request_body_redacted": {"name": "example", "password": "hidden"},
            "response_body_redacted": {"valid": True, "token": "hidden"},
        }
    ])
    assert len(rows) == 1
    row = rows[0]
    assert row["request"]["payload_redacted"]["name"] == "example"
    assert row["request"]["payload_redacted"]["password"] == "***MASKED***"
    assert row["request"]["headers_redacted"]["Authorization"] == "***MASKED***"
    assert row["response"]["status"] == 200
    assert row["response"]["payload_redacted"]["valid"] is True
    assert row["response"]["payload_redacted"]["token"] == "***MASKED***"
    assert row["response"]["headers_redacted"]["Set-Cookie"] == "***MASKED***"


def test_payload_response_index_reports_coverage() -> None:
    rows = build_api_transactions([
        {"request_id": "1", "url": "https://hip.example/a", "method": "GET", "status": 200, "response_body_redacted": {"a": 1}},
        {"request_id": "2", "url": "https://hip.example/b", "method": "POST", "status": 204, "request_body_redacted": {"b": 2}},
    ])
    index = build_payload_response_index(rows)
    assert index["transaction_count"] == 2
    assert index["request_payload_count"] == 1
    assert index["response_status_count"] == 2
    assert index["response_payload_count"] == 1
    assert index["authorization_persisted"] is False


def test_streamlit_mission_command_uses_ui_api_autonomous_profile(tmp_path: Path) -> None:
    for relative in ["config.yaml", "input.json"]:
        (tmp_path / relative).write_text("{}", encoding="utf-8")
    (tmp_path / "golden").mkdir()
    (tmp_path / "uploads").mkdir()
    command = build_mission_command(
        project_root=tmp_path,
        config="config.yaml",
        input_json="input.json",
        runs_dir="runs",
        golden_screenshot_dir="golden",
        upload_assets_dir="uploads",
        api_mode="capture",
    )
    joined = " ".join(command)
    for token in [
        "--autonomous-mission",
        "--agentq-crawler-fusion",
        "--dual-ui-api",
        "--capture-submit-api",
        "--api-capture-best-effort",
        "--bounded-runtime-self-heal",
        "--maximum-observability",
    ]:
        assert token in joined
    assert "--api-mode capture" in joined


def test_streamlit_collectors_read_transactions_and_crosswalk(tmp_path: Path) -> None:
    phase_dir = tmp_path / "rule" / "form_api_intelligence" / "attempt_01"
    phase_dir.mkdir(parents=True)
    (phase_dir / "form_api_transactions.json").write_text(json.dumps({
        "phase": "rule",
        "transactions": [{"transaction_id": "r1", "method": "POST", "endpoint_template": "https://hip/api/rule", "request": {}, "response": {"status": 200}}],
    }), encoding="utf-8")
    (phase_dir / "ui_api_input_crosswalk.json").write_text(json.dumps({
        "phase": "rule",
        "rows": [{"input_path": "$.objects.rule.name", "field_key": "rule_name", "observed_api_key_matches": ["name"]}],
    }), encoding="utf-8")
    transactions = collect_api_transactions(tmp_path)
    crosswalk = collect_crosswalk_rows(tmp_path)
    assert transactions[0]["phase"] == "rule"
    assert transactions[0]["response"]["status"] == 200
    assert crosswalk[0]["field_key"] == "rule_name"


def test_streamlit_input_validation_requires_objects_mapping() -> None:
    assert validate_json_upload(b'{"objects":{"rule":{}}}')["pass"] is True
    assert validate_json_upload(b'{"rule":{}}')["pass"] is False
    assert validate_json_upload(b'[]')["pass"] is False


def test_evidence_archive_includes_api_bundle_and_excludes_browser_profile(tmp_path: Path) -> None:
    run = tmp_path / "run-1"
    (run / "rule").mkdir(parents=True)
    (run / "browser_profile").mkdir()
    (run / "rule" / "form_api_payload_response_bundle.json").write_text("{}", encoding="utf-8")
    (run / "browser_profile" / "cookies.json").write_text("{}", encoding="utf-8")
    archive = build_evidence_archive(run)
    import zipfile
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
    assert any(name.endswith("form_api_payload_response_bundle.json") for name in names)
    assert not any("browser_profile" in name for name in names)


def test_browser_capture_fetches_xhr_and_fetch_response_bodies() -> None:
    source = Path("hip_id_agent/browser_session.py").read_text(encoding="utf-8")
    assert 'resource_type in {"xhr", "fetch"}' in source
    assert "response_body_capture_status=capture_status" in source
    assert "response_body_truncated=truncated" in source


def test_streamlit_entrypoint_and_runner_exist() -> None:
    app = Path("streamlit_app.py").read_text(encoding="utf-8")
    runner = Path("RUN_STREAMLIT_UI.ps1").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "hip_id_agent.streamlit_dashboard" in app
    assert "python -m streamlit run" in runner
    # v1.8.0 uses a JavaScript UI by default; Streamlit is retained only as an optional legacy extra.
    assert "streamlit>=1.40.0" not in requirements
    assert 'legacy-streamlit = ["streamlit>=1.40.0"]' in pyproject
    assert len(PHASES) == 7
