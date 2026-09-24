from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from hip_id_agent.capability_graph import HIPCapabilityGraph
from hip_id_agent.config import AppConfig
from hip_id_agent.full_deep_learning import (
    EXPECTED_ACTIONS,
    FAMILY_SEQUENCE,
    FullHIPDeepLearningMission,
    HIPCapabilityCertifier,
    latest_certification,
)

ROOT = Path(__file__).resolve().parents[1]


def _seed_family(graph: HIPCapabilityGraph, family: str) -> None:
    graph.observe_page(page_family=family, url=f"https://host/{family}", title=family, run_id="seed")
    search = graph.observe_capability(page_family=family, kind="search", label="Search", selector="#search", scope="listing_surface")
    graph.mark_success(search["capability_id"])
    row = graph.observe_capability(page_family=family, kind="row_action", label="Edit", selector="#edit", scope="entity_row")
    graph.mark_success(row["capability_id"])
    for action in EXPECTED_ACTIONS[family]:
        cap = graph.observe_capability(page_family=family, kind="row_action", label=action.title(), selector=f"#{action}", scope="entity_row")
        graph.mark_success(cap["capability_id"])
    if family != "data_maps":
        field = graph.observe_capability(page_family=family, kind="form_field", label="Name", selector="#name", scope="form_surface")
        graph.mark_success(field["capability_id"])
    graph.observe_api_contract(
        page_family=family,
        method="GET",
        url=f"https://host/api/{family}",
        response_status=200,
        caused_by_capability_id=row["capability_id"],
    )


def _seed_replays(graph: HIPCapabilityGraph) -> None:
    graph.observe_replay_profile(page_family="data_maps", name="entity_edit", steps=[{"type": "action", "capability_id": "x"}], verified=True)
    graph.observe_replay_profile(page_family="document_types", name="create_source_document_type", steps=[{"type": "form_field"}], verified=True)
    graph.observe_replay_profile(page_family="document_types", name="create_target_document_type", steps=[{"type": "form_field"}], verified=True)
    graph.observe_replay_profile(page_family="rules", name="create_rule", steps=[{"type": "form_field"}], verified=True)
    graph.observe_replay_profile(page_family="transport_profiles", name="create_source_transport_profile", steps=[{"type": "form_field"}], verified=True)
    graph.observe_replay_profile(page_family="transport_profiles", name="create_target_transport_profile", steps=[{"type": "form_field"}], verified=True)
    graph.observe_replay_profile(page_family="bizflows", name="create_biz_flow", steps=[{"type": "form_field"}], verified=True)


def _fully_seeded(tmp_path: Path) -> HIPCapabilityGraph:
    graph = HIPCapabilityGraph(tmp_path / "portal_brain")
    for family in FAMILY_SEQUENCE:
        _seed_family(graph, family)
    _seed_replays(graph)
    graph.save()
    return graph


def test_full_deep_family_sequence_covers_all_major_hip_surfaces():
    assert list(FAMILY_SEQUENCE) == ["data_maps", "document_types", "rules", "transport_profiles", "bizflows"]


def test_certifier_passes_when_all_core_evidence_exists(tmp_path):
    graph = _fully_seeded(tmp_path)
    summaries = {f: {"safe_discovery": True, "mutation_probe_network_abort": True, "input_values_persisted_to_capability_memory": False} for f in FAMILY_SEQUENCE}
    cert = HIPCapabilityCertifier(graph).certify(summaries, input_contract={"pass": True})
    assert cert["operational_readiness"] is True
    assert cert["full_visible_action_coverage"] is True
    assert cert["blocker_gap_count"] == 0
    assert all(row["operational_ready"] for row in cert["families"].values())


def test_missing_visible_action_is_warning_not_silent_success(tmp_path):
    graph = _fully_seeded(tmp_path)
    # Remove Deploy only from Data Maps; the family stays operational but action coverage is incomplete.
    for cid, cap in list(graph.data["capabilities"].items()):
        if cap.get("page_family") == "data_maps" and str(cap.get("label") or "").lower() == "deploy":
            del graph.data["capabilities"][cid]
    cert = HIPCapabilityCertifier(graph).certify({f: {"safe_discovery": True, "mutation_probe_network_abort": True} for f in FAMILY_SEQUENCE}, input_contract={"pass": True})
    assert cert["operational_readiness"] is True
    assert cert["full_visible_action_coverage"] is False
    assert any(g["family"] == "data_maps" and g["item"] == "deploy" and g["severity"] == "warning" for g in cert["gaps"])


def test_missing_required_replay_is_blocker(tmp_path):
    graph = _fully_seeded(tmp_path)
    graph.data["replay_profiles"] = {k: v for k, v in graph.data["replay_profiles"].items() if v.get("name") != "create_rule"}
    cert = HIPCapabilityCertifier(graph).certify({f: {"safe_discovery": True, "mutation_probe_network_abort": True} for f in FAMILY_SEQUENCE}, input_contract={"pass": True})
    assert cert["operational_readiness"] is False
    assert cert["families"]["rules"]["core_checks"]["required_replay_verified"] is False
    assert any(g["family"] == "rules" and g["type"] == "replay" for g in cert["gaps"])


def test_missing_api_response_or_causality_blocks_family(tmp_path):
    graph = _fully_seeded(tmp_path)
    for row in graph.data["api_contracts"].values():
        if "bizflows" in (row.get("page_families") or []):
            row["response_statuses"] = []
            row["caused_by_capability_ids"] = []
    cert = HIPCapabilityCertifier(graph).certify({f: {"safe_discovery": True, "mutation_probe_network_abort": True} for f in FAMILY_SEQUENCE}, input_contract={"pass": True})
    checks = cert["families"]["bizflows"]["core_checks"]
    assert checks["api_response_evidence"] is False
    assert checks["causal_api_evidence"] is False
    assert cert["operational_readiness"] is False


def test_family_pass_criteria_match_each_deep_learner(tmp_path):
    cfg = AppConfig()
    mission = FullHIPDeepLearningMission(cfg, capability_graph=HIPCapabilityGraph(tmp_path / "brain"))
    assert mission._family_pass("data_maps", {"deterministic_replay": {"verified_count": 1}})
    assert mission._family_pass("document_types", {"phases": {
        "source_document_type": {"create_form_parent_child_learning": {"pass": True}},
        "target_document_type": {"create_form_parent_child_learning": {"pass": True}},
    }})
    assert mission._family_pass("rules", {"create_form_parent_child_learning": {"pass": True}})
    assert mission._family_pass("transport_profiles", {"phases": {
        "source_transport_profile": {"create_form_parent_child_learning": {"pass": True}},
        "target_transport_profile": {"create_form_parent_child_learning": {"pass": True}},
    }})
    assert mission._family_pass("bizflows", {"create_form_parent_child_learning": {"pass": True}})


def test_global_catalogs_are_value_free(tmp_path):
    graph = _fully_seeded(tmp_path)
    cfg = AppConfig()
    mission = FullHIPDeepLearningMission(cfg, capability_graph=graph)
    paths = mission._write_global_catalogs(tmp_path / "run")
    for path in paths.values():
        text = Path(path).read_text(encoding="utf-8")
        assert '"values_stored": false' in text
        assert "U-HAUL" not in text


def test_latest_certification_returns_newest_run(tmp_path):
    runs = tmp_path / "runs"
    a = runs / "run-a"; b = runs / "run-b"
    a.mkdir(parents=True); b.mkdir(parents=True)
    (a / "hip_capability_certification.json").write_text(json.dumps({"operational_readiness": False}), encoding="utf-8")
    (b / "hip_capability_certification.json").write_text(json.dumps({"operational_readiness": True}), encoding="utf-8")
    result = latest_certification(runs)
    assert result["found"] is True
    assert result["run_id"] in {"run-a", "run-b"}
    assert "certification" in result


def test_cli_backend_frontend_and_runner_expose_full_deep(monkeypatch):
    cli = (ROOT / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    runner = (ROOT / "RUN_LEARN_HIP_FULL_DEEP.ps1").read_text(encoding="utf-8")
    assert '@app.command("learn-hip-full-deep")' in cli
    assert '@app.command("full-deep-readiness")' in cli
    assert "Deep Learn ALL HIP + Certify" in frontend
    assert "/api/full-deep/start" in frontend
    assert "Full HIP Capability Certification" in frontend
    assert "learn-hip-full-deep" in runner
    assert "HIP_REQUIRE_AUTOGEN_075" in runner

    import backend.app as backend_app
    captured = {}
    monkeypatch.setattr(backend_app, "_start_cli", lambda command, runs_dir="": captured.update(command=command, runs_dir=runs_dir) or {"command": command})
    client = TestClient(backend_app.app)
    resp = client.post("/api/full-deep/start", json={"config": "config.yaml", "input_json": "x.json", "runs_dir": "runs", "require_mcp": True, "continue_on_family_failure": True})
    assert resp.status_code == 200
    assert "learn-hip-full-deep" in captured["command"]
    assert "--require-mcp" in captured["command"]
    assert "--continue-on-family-failure" in captured["command"]


def test_full_deep_source_reuses_one_persistent_sso_profile_policy():
    source = (ROOT / "hip_id_agent" / "full_deep_learning.py").read_text(encoding="utf-8")
    assert "persistent_sso_profile" in source
    assert "one authenticated persistent Chrome profile" in source
    assert "DataMapDeepDiscoveryFlow" in source
    assert "DocumentTypeDeepDiscoveryFlow" in source
    assert "RuleDeepDiscoveryFlow" in source
    assert "TransportProfileDeepDiscoveryFlow" in source
    assert "BizFlowDeepDiscoveryFlow" in source


def test_full_mission_writes_state_certification_and_gap_queue_without_live_browser(tmp_path):
    from hip_id_agent.models import RunContext

    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    cfg.reporting.runs_dir = str(tmp_path / "runs")
    cfg.portal.browser_user_data_dir = str(tmp_path / "browser_profile")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "portal_brain")
    mission = FullHIPDeepLearningMission(cfg, capability_graph=graph)

    class FakeFlow:
        def __init__(self, config, capability_graph=None):
            self.graph = capability_graph
        async def run(self, *, ctx, input_json):
            family = self.family
            _seed_family(self.graph, family)
            # Required replay evidence for the certifier.
            if family == "data_maps":
                self.graph.observe_replay_profile(page_family=family, name="entity_edit", steps=[{"type":"action"}], verified=True)
                return {"safe_discovery": True, "mutation_probe_network_abort": True, "deterministic_replay": {"verified_count": 1}}
            if family == "document_types":
                self.graph.observe_replay_profile(page_family=family, name="create_source_document_type", steps=[{"type":"form_field"}], verified=True)
                self.graph.observe_replay_profile(page_family=family, name="create_target_document_type", steps=[{"type":"form_field"}], verified=True)
                return {"safe_discovery": True, "mutation_probe_network_abort": True, "phases": {
                    "source_document_type": {"create_form_parent_child_learning": {"pass": True}},
                    "target_document_type": {"create_form_parent_child_learning": {"pass": True}},
                }}
            if family == "rules":
                self.graph.observe_replay_profile(page_family=family, name="create_rule", steps=[{"type":"form_field"}], verified=True)
                return {"safe_discovery": True, "mutation_probe_network_abort": True, "create_form_parent_child_learning": {"pass": True}}
            if family == "transport_profiles":
                self.graph.observe_replay_profile(page_family=family, name="create_source_transport_profile", steps=[{"type":"form_field"}], verified=True)
                self.graph.observe_replay_profile(page_family=family, name="create_target_transport_profile", steps=[{"type":"form_field"}], verified=True)
                return {"safe_discovery": True, "mutation_probe_network_abort": True, "phases": {
                    "source_transport_profile": {"create_form_parent_child_learning": {"pass": True}},
                    "target_transport_profile": {"create_form_parent_child_learning": {"pass": True}},
                }}
            self.graph.observe_replay_profile(page_family=family, name="create_biz_flow", steps=[{"type":"form_field"}], verified=True)
            return {"safe_discovery": True, "mutation_probe_network_abort": True, "create_form_parent_child_learning": {"pass": True}}

    factories = {}
    for family in FAMILY_SEQUENCE:
        cls = type(f"Fake_{family}", (FakeFlow,), {"family": family})
        factories[family] = cls
    mission.flow_factories = factories

    run_dir = tmp_path / "runs" / "full"
    ctx = RunContext(run_id="full", customer="TEST", partner_query="", system_query="", run_dir=run_dir, screenshots_dir=run_dir / "screenshots")
    import asyncio
    result = asyncio.run(mission.run(ctx=ctx, input_json=ROOT / "examples" / "uhaul_poasn_full_dummy_input.json"))
    assert result["status"] == "complete"
    assert result["certification"]["operational_readiness"] is True
    assert (run_dir / "full_deep_learning_mission_state.json").is_file()
    assert (run_dir / "hip_capability_certification.json").is_file()
    assert (run_dir / "hip_capability_gap_queue.json").is_file()
    assert (run_dir / "hip_global_api_catalog.json").is_file()
    assert (run_dir / "hip_replay_registry.json").is_file()


def test_full_mission_resume_adopts_verified_family_summary(tmp_path):
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    graph = HIPCapabilityGraph(Path(cfg.reporting.memory_dir) / "portal_brain")
    mission = FullHIPDeepLearningMission(cfg, capability_graph=graph)
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "datamap_deep_discovery_summary.json").write_text(json.dumps({"deterministic_replay": {"verified_count": 1}}), encoding="utf-8")
    loaded = mission._load_resume_summary(prior, "data_maps")
    assert loaded is not None
    assert mission._family_pass("data_maps", loaded) is True
