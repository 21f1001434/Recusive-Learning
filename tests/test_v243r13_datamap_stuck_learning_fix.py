"""V243R13: Data Map learning phase stuck in retry_required.

Live symptom: "Data Map autonomous goal was not proven ... cycles 1-5
retry_required, failed_attempts: []" while the Create Map drawer showed the
expected "Map identifier already exists" message (same as the golden image).

Root causes reproduced against a real Chromium replica of the drawer:
* ``mask_sensitive_data`` matched ``auth`` inside ``authoritative_*`` and turned
  the strict proof flag ``True`` into ``"***MASKED***"``, so no cycle could pass;
* a disabled Map Identifier Version showing ``1`` only as a placeholder could
  never satisfy its verify-only node;
* failures reported ``failed_attempts: []`` because the failure result had no
  execution attached.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hip_id_agent.autonomous_form_runtime import (
    autonomous_failure_summary,
    autonomous_target_execution,
    execute_autonomous_phase_goal,
)
from hip_id_agent.security import mask_sensitive_data
from hip_id_agent.stateful_form_runtime import _stateful_value_equal, compile_phase_state_graph

FIXTURE = Path(__file__).parent / "fixtures" / "create_map_duplicate_identifier.html"
INPUT = {"objects": {"data_map": {
    "map_identifier": "DELLCoXMLASNXX08C_U-HAUL",
    "map_identifier_version": "1",
    "status": "Enable",
    "map_name": "DELLCoXMLASNXX08C",
    "map_class": "Transform_DELLCoXMLASNXX08C",
}}}


def _chromium_path() -> str | None:
    for candidate in (os.environ.get("HIP_TEST_CHROMIUM"), "/opt/pw-browsers/chromium", "/usr/bin/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _run_replica(version_attr: str, tmp_path: Path, max_cycles: int = 2) -> dict:
    async def run() -> dict:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, executable_path=_chromium_path())
            except Exception as exc:  # pragma: no cover - environment without Chromium
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(FIXTURE.read_text(encoding="utf-8").replace("__VERSION_ATTR__", version_attr))
            try:
                return await execute_autonomous_phase_goal(
                    page=page, graph=compile_phase_state_graph(INPUT, "data_map"), phase="data_map",
                    input_data=INPUT, config=None, output_dir=tmp_path, max_cycles=max_cycles,
                )
            finally:
                await browser.close()
    return asyncio.run(run())


def test_authoritative_proof_flags_are_not_masked_but_secrets_are():
    masked = mask_sensitive_data({
        "authoritative_execution_verified": True,
        "authoritative_execution": True,
        "authoritative_exact_attempt_count": 3,
        "session_reused": True,
        "token_present": None,
        "authorization": "Bearer abc",
        "auth": "abc",
        "x-auth-token": "abc",
        "oauth_client": "abc",
        "session": "cookie-value",
        "nested": {"sftp_password": "pw", "client_secret": 12345},
    })
    assert masked["authoritative_execution_verified"] is True
    assert masked["authoritative_execution"] is True
    assert masked["authoritative_exact_attempt_count"] == 3
    assert masked["session_reused"] is True
    assert masked["token_present"] is None
    for key in ("authorization", "auth", "x-auth-token", "oauth_client", "session"):
        assert masked[key] == "***MASKED***", key
    assert masked["nested"] == {"sftp_password": "***MASKED***", "client_secret": "***MASKED***"}


def test_verify_only_readonly_placeholder_counts_only_for_portal_owned_controls():
    node = {"phase": "data_map", "field_key": "map_identifier_version", "action": "verify_only", "expected_value": "1"}
    assert _stateful_value_equal(node, {"value": "", "placeholder": "1", "disabled": True})
    assert _stateful_value_equal(node, {"value": "", "placeholder": "1.0", "readonly": True})
    # An editable control's placeholder is only a hint, never a value.
    assert not _stateful_value_equal(node, {"value": "", "placeholder": "1"})
    # A real value always wins over the placeholder.
    assert not _stateful_value_equal(node, {"value": "2", "placeholder": "1", "disabled": True})
    # Any portal-owned (disabled) field shows its value this way, e.g. Rule Scope.
    scope = dict(node, field_key="rule_scope", action="select_single", expected_value="GLOBAL")
    assert _stateful_value_equal(scope, {"value": "", "placeholder": "GLOBAL", "disabled": True})
    assert not _stateful_value_equal(scope, {"value": "", "placeholder": "GLOBAL"})


@pytest.mark.parametrize("version_attr", ['value="1"', 'placeholder="1"'])
def test_datamap_goal_is_proven_despite_existing_identifier_warning(tmp_path: Path, version_attr: str):
    result = _run_replica(version_attr, tmp_path)
    assert result["pass"] is True, autonomous_failure_summary(result)
    assert [c["status"] for c in result["cycles"]] == ["goal_achieved"]
    stage = result["final_execution"]["execution_stage_audit"]
    assert stage["exact_execution_verified"] is True
    assert stage["authoritative_execution_verified"] is True


def test_datamap_failure_names_the_failing_field_instead_of_empty_list(tmp_path: Path):
    result = _run_replica('value="2"', tmp_path, max_cycles=2)
    assert result["pass"] is False
    target = autonomous_target_execution(result)
    assert target["pass"] is False
    summary = target["autonomous_failure_summary"]
    fields = [a["field"] for a in summary["failed_attempts"]]
    assert "map_identifier_version" in fields
    version = next(a for a in summary["failed_attempts"] if a["field"] == "map_identifier_version")
    assert version["reason"].startswith("HIP_READONLY_PORTAL_VALUE_MISMATCH")
    assert all("executor_pass" in c["unmet"] for c in summary["cycles"])
    assert target["failed_attempts"] == summary["failed_attempts"]


def test_target_execution_helper_never_turns_failure_into_proof():
    proven = {"pass": True, "final_execution": {"pass": True, "attempts": [1]}}
    assert autonomous_target_execution(proven) == {"pass": True, "attempts": [1]}
    disabled_fallback = {
        "pass": False, "status": "disabled_fallback",
        "final_execution": {"pass": False, "failed_attempts": [{"field": "map_name", "reason": "x"}]},
    }
    stub = autonomous_target_execution(disabled_fallback)
    assert stub["pass"] is False
    assert stub["failed_attempts"][0]["field"] == "map_name"
    assert autonomous_target_execution({})["pass"] is False
