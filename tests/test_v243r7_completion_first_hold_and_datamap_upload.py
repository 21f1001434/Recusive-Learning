from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.upload_assets import find_upload_asset
from hip_id_agent.human_phase_review import HumanPhaseReviewStore
from hip_id_agent.dummy_fill_e2e import phase_exact_completion_checkpoint, _hold_incomplete_phase_for_human
from hip_id_agent import autonomous_form_runtime as afr


def test_explicit_upload_path_is_authoritative_even_outside_uploads(tmp_path: Path):
    jar = tmp_path / "external" / "Transform_UHAUL.jar"
    jar.parent.mkdir()
    jar.write_bytes(b"jar")
    input_json = tmp_path / "input.json"
    input_json.write_text("{}", encoding="utf-8")
    asset = find_upload_asset(
        {"_input_json_path": str(input_json)},
        str(jar),
        field_key="map_data_file",
        phase="data_map",
        accepted_extensions=[".jar"],
        require_explicit=True,
    )
    assert asset is not None
    assert Path(asset["path"]) == jar.resolve()
    assert asset["source"] == "explicit_input_path"


def _write_execution(path: Path, attempts):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pass": True,
        "status": "pass",
        "strict_live_execution": True,
        "attempts": attempts,
        "failed_attempts": [],
        "execution_stage_audit": {
            "fields_filled_or_verified": True,
            "exact_execution_verified": True,
            "authoritative_execution_verified": True,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_datamap_exact_checkpoint_requires_upload_proof(tmp_path: Path):
    phase_dir = tmp_path / "data_map"
    artifact = phase_dir / "datamap_kb" / "datamap_target_branch_execution.json"
    _write_execution(artifact, [
        {"field": "map_name", "success": True, "exact_verified": True, "actual_value": "MAP"},
        {"field": "map_data_file", "success": False, "exact_verified": False, "actual_value": ""},
    ])
    result = phase_exact_completion_checkpoint("data_map", phase_dir)
    assert result["pass"] is False
    assert result["inspected"][0]["required_upload_proof"]["required"] is True
    assert result["inspected"][0]["required_upload_proof"]["pass"] is False


def test_datamap_exact_checkpoint_accepts_exact_upload_proof(tmp_path: Path):
    phase_dir = tmp_path / "data_map"
    artifact = phase_dir / "datamap_kb" / "datamap_target_branch_execution.json"
    _write_execution(artifact, [
        {"field": "map_name", "success": True, "exact_verified": True, "actual_value": "MAP"},
        {
            "field": "map_data_file", "success": True, "exact_verified": True,
            "actual_value": "Transform_UHAUL.jar", "executor": "phase-specific-contract-aware-uploader",
        },
    ])
    result = phase_exact_completion_checkpoint("data_map", phase_dir)
    assert result["pass"] is True
    assert result["inspected"][0]["required_upload_proof"]["pass"] is True


@pytest.mark.asyncio
async def test_autonomous_upload_falls_back_to_phase_aware_file_driver(monkeypatch, tmp_path: Path):
    jar = tmp_path / "Transform_UHAUL.jar"
    jar.write_bytes(b"jar")
    graph = {
        "phase": "data_map",
        "nodes": [{
            "node_id": "data_map.create_map.map_data_file",
            "field_key": "map_data_file",
            "section": "Create Map",
            "action": "upload_file",
            "expected_value": str(jar),
            "required": True,
            "semantic_locator": {"labels": ["Map Data"], "names": ["mapData"], "roles": []},
            "depends_on": [],
        }],
    }
    controls = []
    calls = []

    async def fake_gate(page, phase):
        return {"fatal": []}

    async def fake_capture(page, phase):
        return list(controls)

    def fake_diag(rows, node):
        return {"resolved": False, "reason": "hidden DDS input not represented yet"}

    async def fake_root(page, phase):
        return None

    async def fake_upload(page, root, field, path, phase=""):
        calls.append((field, path, phase))
        return {
            "field": "map_data_file", "success": True, "filled": True,
            "uploaded_file_name": Path(path).name, "file_input_selector": 'input[name="mapData"]',
        }

    async def fake_execute(page, g, **kwargs):
        # The full goal execution receives the successful upload as prior_attempts.
        prior = kwargs.get("prior_attempts") or []
        ok = any(a.get("field") == "map_data_file" and a.get("success") for a in prior)
        return {
            "pass": ok,
            "attempts": [{
                "node_id": "data_map.create_map.map_data_file", "field": "map_data_file",
                "success": ok, "exact_verified": ok, "authoritative_execution": ok,
                "actual_value": jar.name if ok else "",
            }],
            "failed_attempts": [] if ok else [{"field": "map_data_file"}],
            "execution_stage_audit": {
                "fields_filled_or_verified": ok, "exact_execution_verified": ok,
                "authoritative_execution_verified": ok, "input_owned_unresolved_node_ids": [] if ok else ["upload"],
            },
        }

    monkeypatch.setattr(afr, "assert_active_surface", fake_gate)
    monkeypatch.setattr(afr, "capture_stateful_controls", fake_capture)
    monkeypatch.setattr(afr, "resolve_stateful_control_diagnostics", fake_diag)
    monkeypatch.setattr(afr, "get_active_form_root", fake_root)
    monkeypatch.setattr(afr, "upload_file_control", fake_upload)
    monkeypatch.setattr(afr, "execute_phase_state_graph", fake_execute)

    cfg = AppConfig()
    cfg.aia.enabled = False
    result = await afr.execute_autonomous_phase_goal(
        page=SimpleNamespace(), graph=graph, phase="data_map",
        input_data={"objects": {"data_map": {"map_data_file": str(jar)}}},
        config=cfg, output_dir=tmp_path / "runtime", max_cycles=1,
    )
    assert calls
    assert calls[0][0] == "map_data_file"
    assert result["pass"] is True


class _FakeBrowser:
    page = None
    async def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"png")


@pytest.mark.asyncio
async def test_incomplete_phase_hold_keeps_browser_and_resumes_same_phase(tmp_path: Path):
    cfg = AppConfig()
    cfg.human_in_the_loop.hold_browser_on_incomplete_phase = True
    cfg.human_in_the_loop.incomplete_phase_wait_seconds = 1
    cfg.human_in_the_loop.incomplete_phase_poll_seconds = 0.01
    store = HumanPhaseReviewStore(tmp_path / "reviews", cfg.human_in_the_loop)

    # Resolve immediately after creation by wrapping the store method.
    original = store.create_recovery_request
    def create_and_resolve(**kwargs):
        row = original(**kwargs)
        store.resolve(request_id=row["request_id"], verdict="looks_correct", note="manual fix done")
        return row
    store.create_recovery_request = create_and_resolve  # type: ignore[assignment]

    result = await _hold_incomplete_phase_for_human(
        store=store, browser=_FakeBrowser(), run_id="RUN1", phase="data_map",
        phase_display="Data Map", recovery_round=2, phase_dir=tmp_path / "phase",
        reason="upload not committed", exact_checkpoint={"pass": False},
        automated_judge={}, verification={}, config=cfg,
    )
    assert result["held"] is True
    assert result["resume"] is True
    wait = json.loads((tmp_path / "phase" / "INCOMPLETE_PHASE_WAITING.json").read_text())
    assert wait["status"] == "resolved_recheck_same_phase"
    assert wait["browser_kept_open"] is True
