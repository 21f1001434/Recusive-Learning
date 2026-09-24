from pathlib import Path

from hip_id_agent.section_judge import (
    DualModelSectionJudge,
    SectionJudgePolicy,
    _verification_status_is_success,
)


def _verification(status: str, screenshot: Path):
    return {
        "phase": "source_document_type",
        "status": status,
        "failed_attempts": [],
        "warnings": ["Many dropdown controls have no captured/enriched options."],
        "screenshots": [str(screenshot)],
        "counts": {"form_controls": 29},
        "validation_gate": {"blocking": [], "accepted_nonblocking": []},
        "object_resolution": {},
        "actual_state": {
            "source": "locked_filled_form.html",
            "visible_text": "Create Document Type Document Type Details Name Transaction Type Version Data Format Type Document Identifier Attributes to Configure Validation Cancel Submit",
            "row_counts": {"document_identifier": 1, "attribute": 5},
            "controls": [
                {"label": "Name"},
                {"label": "Transaction Type"},
                {"label": "Version"},
                {"label": "Data Format Type"},
            ],
        },
        "all_attempts": [],
    }


def _exact_result():
    return {
        "pass": True,
        "missing_values": [],
        "row_issues": [],
        "failed_attempts": [],
        "matched_values": [
            {
                "field": "document_type_version",
                "expected": "1",
                "actual": "1.0",
                "selector": "input#version",
                "evidence": "captured_form_inventory",
                "section": "Document Type Details",
            },
            {
                "field": "document_identifier.operation",
                "expected": "All conditions are satisfied",
                "actual": "All conditions are satisfied",
                "selector": "input#operation",
                "evidence": "saved_post_fill_dom",
                "section": "Document Identifier",
            },
            {
                "field": "attributes[1].expression",
                "expected": "/DellAutoASN/ShipNoticeHeader/From/PartnerInfomation/PartnerIdentifier",
                "actual": "/DellAutoASN/ShipNoticeHeader/From/PartnerInfomation/PartnerIdentifier",
                "selector": "input#sender-expression",
                "evidence": "saved_post_fill_dom",
                "section": "Attributes to Configure",
                "row_kind": "attribute",
                "row_index": 1,
            },
        ],
    }


def test_pass_with_warnings_is_a_successful_verification_state():
    assert _verification_status_is_success("pass") is True
    assert _verification_status_is_success("pass_with_warnings") is True
    assert _verification_status_is_success("failed") is False


def test_exact_completed_doctype_reconciles_text_and_vision_contradictions(monkeypatch, tmp_path):
    screenshot = tmp_path / "filled.png"
    screenshot.write_bytes(b"not-an-image-but-present")

    judge = DualModelSectionJudge(
        SectionJudgePolicy(enabled=True, require_text_model=True, require_vision_model=True, fail_closed=True)
    )
    monkeypatch.setattr(judge, "deterministic_judge", lambda **_: _exact_result())
    monkeypatch.setattr(
        judge,
        "_text_judge",
        lambda _payload: {
            "status": "ok",
            "pass": False,
            "summary": "Form validation failed due to mismatched required fields.",
            "missing_or_wrong": [
                {
                    "field": "document_type_version",
                    "expected": "1",
                    "actual": "1.0",
                    "reason": "exact string mismatch",
                },
                {
                    "field": "document_identifier_operation",
                    "expected": "All conditions are satisfied",
                    "actual": "all conditions are satisfied",
                    "reason": "case mismatch",
                },
            ],
            "repair_steps": [{"field": "document_type_version", "action": "refill", "value": "1"}],
        },
    )
    monkeypatch.setattr(
        judge,
        "_vision_judge",
        lambda **_: {
            "status": "ok",
            "pass": False,
            "summary": "Mismatch in Sender expression.",
            "visible_issues": [
                {
                    "field": "attributes_1_expression",
                    "expected": "/DellAutoASN/ShipNoticeHeader/From/PartnerInfomation/PartnerIdentifier",
                    "observed": "/DellAutoASN/ShipNoticeHeader/From/PartnerInformation/PartnerIdentifier",
                    "reason": "offscreen screenshot interpretation",
                }
            ],
        },
    )

    result = judge.judge_artifact_section(
        phase="source_document_type",
        section="Source Document Type",
        expected_input={"objects": {"source_document_type": {}}},
        verification=_verification("pass_with_warnings", screenshot),
        screenshot=str(screenshot),
        golden_screenshots=[],
    )

    assert result["pass"] is True
    assert result["status"] == "pass"
    assert result["deterministic_judge"]["pass"] is True
    assert result["deterministic_judge"]["verification_status"] == "pass_with_warnings"
    assert result["text_model_judge"]["status"] == "reconciled"
    assert result["text_model_judge"]["pass"] is True
    assert result["text_model_judge"]["repair_steps"] == []
    assert len(result["text_model_judge"]["unsupported_model_issues"]) == 2
    assert result["vision_model_judge"]["status"] == "reconciled"
    assert result["vision_model_judge"]["pass"] is True
    assert len(result["vision_model_judge"]["unsupported_model_issues"]) == 1


def test_failed_verification_remains_fail_closed_even_when_models_are_reconcilable(monkeypatch, tmp_path):
    screenshot = tmp_path / "filled.png"
    screenshot.write_bytes(b"present")
    judge = DualModelSectionJudge(
        SectionJudgePolicy(enabled=True, require_text_model=True, require_vision_model=True, fail_closed=True)
    )
    monkeypatch.setattr(judge, "deterministic_judge", lambda **_: _exact_result())
    monkeypatch.setattr(judge, "_text_judge", lambda _payload: {"status": "ok", "pass": True, "missing_or_wrong": []})
    monkeypatch.setattr(judge, "_vision_judge", lambda **_: {"status": "ok", "pass": True, "visible_issues": []})

    result = judge.judge_artifact_section(
        phase="source_document_type",
        section="Source Document Type",
        expected_input={"objects": {"source_document_type": {}}},
        verification=_verification("failed", screenshot),
        screenshot=str(screenshot),
        golden_screenshots=[],
    )
    assert result["pass"] is False
    assert result["deterministic_judge"]["pass"] is False
