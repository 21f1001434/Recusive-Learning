"""V243R19: learned form skills are saved only after a deterministic replay proves them.

Unit checks of the skill library (no browser): the candidate -> certified ->
stale lifecycle, re-learning on new fields and new branches, learning which
choice fields are branch fields, and that nothing stored carries input values.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from hip_id_agent.autonomous_form_runtime import _flatten_phase_input_leaves
from hip_id_agent.config import AppConfig
from hip_id_agent.dds_control_driver import _value_matches_variants
from hip_id_agent.form_structure_memory import FormStructureMemory
from hip_id_agent.portal_operations import form_phase_for, operation_gate
from hip_id_agent.portal_skills import (
    PortalSkillStore,
    SkillSession,
    _strip_identity,
    canonical_operation,
    operation_specs,
    resolve_operation,
)

PHASE = "source_transport_profile"


def _config(tmp_path: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.reporting.memory_dir = str(tmp_path / "memory")
    return cfg


def _data(**values):
    obj = {"profile_name": "TP_ALPHA", "profile_usage": "Sender", "interface_type": "SFTP HAFT", "interface_environment": "UAT"}
    obj.update(values)
    return {"objects": {PHASE: obj}}


def _graph(data):
    actions = {"profile_name": "fill_text", "as2_identifier": "fill_text"}
    nodes = []
    for key, value in data["objects"][PHASE].items():
        nodes.append({"node_id": f"{PHASE}.x.{key}", "field_key": key, "input_path": f"$.objects.{PHASE}.{key}",
                      "action": actions.get(key, "select_single"), "expected_value": value, "section": "Create Transport Profile"})
    return {"nodes": nodes}


def _identity(key: str, row: str = "0", label: str = "") -> str:
    return "|".join(["create_transport_profile", "", row, key, key.replace("_", ""), "input", "combobox", label or key])


def _execution(graph):
    return {"attempts": [
        {"node_id": n["node_id"], "input_path": n["input_path"], "success": True, "exact_verified": True,
         "binding_diagnostics": {"selected_identity": _identity(n["field_key"])}}
        for n in graph["nodes"]
    ]}


def _begin(cfg, data, **kw):
    graph = _graph(data)
    return SkillSession.begin(config=cfg, page=None, phase=PHASE, section=None, graph=graph,
                              leaves=_flatten_phase_input_leaves(data, PHASE), input_data=data, **kw), graph


def _learn(cfg, data, *, surface=("a", "b")):
    session, graph = _begin(cfg, data)
    candidate = session.build_candidate(graph=graph, final_execution=_execution(graph), cycles=[], controls_after=[],
                                        duration_seconds=20.0)
    candidate["surface"] = list(surface)
    return session, candidate


# ---------------------------------------------------------------- operations
def test_operations_are_read_from_input_json():
    assert canonical_operation("Merger") == "merge" and canonical_operation("update") == "edit"
    assert canonical_operation("Copy") == "clone" and canonical_operation("") == "create"
    assert canonical_operation("Archive") == "archive"  # a portal action not in the vocabulary yet
    data = {"objects": {PHASE: {}}, "operations": [{"phase": PHASE, "operation": "Deploy", "target": "TP"}]}
    assert operation_specs(data)[0]["operation"] == "deploy"
    assert resolve_operation(data, PHASE) == "deploy"
    assert resolve_operation({"operations": {PHASE: "clone"}}, PHASE) == "clone"
    assert resolve_operation({"objects": {PHASE: {"_operation": "edit"}}}, PHASE) == "edit"
    assert resolve_operation({"objects": {PHASE: {}}}, PHASE) == "create"
    # Row actions with their own dialog are learned as their own form.
    assert form_phase_for(PHASE, "edit", {}) == PHASE
    assert form_phase_for(PHASE, "deploy", {}) == f"universal_{PHASE}_deploy"


def test_metadata_keys_are_not_form_fields():
    leaves = _flatten_phase_input_leaves({"objects": {PHASE: {"profile_name": "X", "_operation": "edit", "_target": "Y"}}}, PHASE)
    assert [leaf["field_key"] for leaf in leaves] == ["profile_name"]


def test_commit_needs_all_three_gates(monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    assert operation_gate(True, "ALLOW HIP MUTATION")["pass"] is True
    assert operation_gate(False, "ALLOW HIP MUTATION")["pass"] is False
    assert operation_gate(True, "yes please")["pass"] is False
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "")
    assert operation_gate(True, "ALLOW HIP MUTATION")["pass"] is False


# ---------------------------------------------------------------- lifecycle
def test_learning_is_saved_only_after_a_replay_proves_it(tmp_path: Path):
    cfg = _config(tmp_path)
    session, candidate = _learn(cfg, _data())
    assert (session.plan, session.reason) == ("learn", "no_skill")
    staged = session.stage(candidate)
    assert staged["status"] == "candidate"
    # Nothing is learned yet: the form structure memory is untouched.
    assert not (tmp_path / "memory" / "form_structure_memory").exists()

    replay, _ = _begin(cfg, _data())
    assert (replay.plan, replay.reason) == ("replay", "candidate_awaiting_replay_proof")
    outcome = replay.record_replay(seconds=5.0, surface=["a", "b"])
    assert outcome["status"] == "certified"
    skill = json.loads((tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text())["skills"][candidate["skill_id"]]
    assert skill["status"] == "certified" and skill["stats"]["speedup"] == 4.0

    again, _ = _begin(cfg, _data(profile_name="TP_OTHER"))  # a different name: same form, same branch
    assert (again.plan, again.reason) == ("replay", "certified_skill")


def test_an_unproven_candidate_that_does_not_replay_is_discarded(tmp_path: Path):
    cfg = _config(tmp_path)
    session, candidate = _learn(cfg, _data())
    session.stage(candidate)
    replay, _ = _begin(cfg, _data())
    replay.replay_failed([{"kind": "replay_failed"}])
    data = json.loads((tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text())
    assert data["skills"] == {}
    assert data["history"][-1]["event"] == "candidate_discarded"


def test_a_certified_skill_that_stops_replaying_goes_stale_and_is_relearned(tmp_path: Path):
    cfg = _config(tmp_path)
    session, candidate = _learn(cfg, _data())
    session.certify(candidate, proof={"kind": "in_run_replay", "seconds": 4})
    replay, _ = _begin(cfg, _data())
    replay.replay_failed([{"kind": "binding_changed", "fields": ["profile_usage"]}])
    after, _ = _begin(cfg, _data())
    assert (after.plan, after.reason) == ("learn", "no_skill")
    skill = json.loads((tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text())["skills"][candidate["skill_id"]]
    assert skill["status"] == "stale" and skill["stale_reason"] == ["binding_changed"]


def test_a_new_input_field_sends_the_run_back_to_learning_and_teaches_the_branch_field(tmp_path: Path):
    cfg = _config(tmp_path)
    session, candidate = _learn(cfg, _data())
    session.certify(candidate, proof={"kind": "in_run_replay", "seconds": 4})
    # AS2 reveals fields of its own; the input now carries them.
    as2 = _data(interface_type="AS2", as2_identifier="AS2-UHAUL-01")
    as2["objects"][PHASE].pop("interface_environment")
    relearn, _ = _begin(cfg, as2)
    assert (relearn.plan, relearn.reason) == ("learn", "new_input_field")
    assert relearn.novelty[0]["fields"] == ["as2_identifier"]
    branch_fields = json.loads((tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text())["branch_fields"]
    assert "interface_type" in branch_fields
    # The SFTP skill still holds for the SFTP branch.
    sftp, _ = _begin(cfg, _data())
    assert (sftp.plan, sftp.reason) == ("replay", "certified_skill")


def test_branch_fields_are_learned_from_form_shapes_and_dropped_when_shapes_match(tmp_path: Path):
    cfg = _config(tmp_path)
    s1, c1 = _learn(cfg, _data(), surface=("name", "usage", "environment"))
    s1.certify(c1, proof={"kind": "in_run_replay", "seconds": 4})
    s2, c2 = _learn(cfg, _data(interface_type="AS2", profile_usage="Receiver"), surface=("name", "usage", "as2 identifier"))
    s2.certify(c2, proof={"kind": "in_run_replay", "seconds": 4})
    fields = json.loads((tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text())["branch_fields"]
    assert {"interface_type", "profile_usage"} <= set(fields)
    # Receiver with SFTP has the SFTP shape: usage does not change the form.
    s3, c3 = _learn(cfg, _data(profile_usage="Receiver"), surface=("name", "usage", "environment"))
    s3.certify(c3, proof={"kind": "in_run_replay", "seconds": 4})
    fields = json.loads((tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text())["branch_fields"]
    assert "profile_usage" not in fields and "interface_type" in fields


def test_a_replay_is_deterministic_only_with_the_same_bindings():
    session = SkillSession()
    session.skill = {"bindings": {
        "profile_usage": {"identity": _strip_identity(_identity("profile_usage")), "choice": False},
        "tags[0].key": {"identity": _strip_identity(_identity("key", row="fp-values-1")), "choice": False},
    }}
    same = [{"input_path": f"$.objects.{PHASE}.tags[0].key", "success": True,
             "binding_diagnostics": {"selected_identity": _identity("key", row="fp-values-2")}}]
    assert session.binding_changes(same) == []  # a row fingerprint made of values is ignored
    moved = [{"input_path": f"$.objects.{PHASE}.profile_usage", "success": True,
              "binding_diagnostics": {"selected_identity": _identity("partner_name")}}]
    assert session.binding_changes(moved)[0]["field"] == "profile_usage"


def test_stored_skills_are_value_free(tmp_path: Path):
    cfg = _config(tmp_path)
    data = _data(interface_type="AS2", as2_identifier="AS2-UHAUL-01", profile_name="TP_SECRET_NAME")
    session, candidate = _learn(cfg, data)
    session.certify(candidate, proof={"kind": "in_run_replay", "seconds": 4})
    text = (tmp_path / "memory" / "portal_skills" / f"{PHASE}.json").read_text()
    for value in ("AS2-UHAUL-01", "TP_SECRET_NAME", "SFTP HAFT", "Sender"):
        assert value not in text
    assert json.loads(text)["skills"][candidate["skill_id"]]["values_stored"] is False


def test_commit_label_is_remembered_per_skill(tmp_path: Path):
    cfg = _config(tmp_path)
    session, candidate = _learn(cfg, _data())
    session.certify(candidate, proof={"kind": "in_run_replay", "seconds": 4})
    store = PortalSkillStore(tmp_path / "memory")
    assert store.commit_label(PHASE, candidate["skill_id"]) == ""
    store.record_commit(PHASE, candidate["skill_id"], "Update")
    assert store.commit_label(PHASE, candidate["skill_id"]) == "Update"


def test_structure_memory_merge_is_split_from_extraction(tmp_path: Path):
    graph = {"nodes": [{"node_id": f"{PHASE}.runtime_input.abc", "input_path": f"$.objects.{PHASE}.as2_identifier",
                        "field_key": "as2_identifier", "action": "fill_text", "semantic_locator": {"labels": ["AS2 Identifier *"]}}]}
    learning = FormStructureMemory.extract_learning(
        graph=graph, final_execution={"attempts": [{"node_id": f"{PHASE}.runtime_input.abc", "success": True}]}, cycles=[])
    assert learning["fields"]["as2_identifier"]["labels"] == ["AS2 Identifier *"]
    memory = FormStructureMemory(tmp_path)
    assert not memory._file(PHASE).exists()
    assert memory.merge_learning(PHASE, learning)["learned_fields"] == 1
    assert memory.load(PHASE)["fields"]["as2_identifier"]["success_count"] == 1


# ---------------------------------------------------------------- live-portal fixes found by R19
def test_none_is_a_real_option_when_it_is_asked_for():
    assert _value_matches_variants("None", ["None"]) is True
    assert _value_matches_variants("None", ["Delete"]) is False
    assert _value_matches_variants("Select", ["Delete"]) is False


def test_certified_replay_skips_the_per_action_model_call():
    from hip_id_agent.browser_session import BrowserSession

    calls = []

    class Bridge:
        primary_framework = True

        async def primary_decide(self, **kw):
            calls.append(kw)
            return {"status": "approved"}

    session = BrowserSession.__new__(BrowserSession)
    session.autowebglm_bridge = Bridge()
    session.page = object()
    session.deterministic_replay_active = True
    decision = asyncio.run(session._autowebglm_primary_decision(action="click", selector="#x"))
    assert decision["reason"] == "certified deterministic replay" and calls == []


def test_edit_and_clone_surfaces_pass_the_transport_profile_gate():
    from hip_id_agent import dds_control_driver as driver

    async def run(title):
        original = driver.active_form_text

        async def text(page, phase):
            return f"{title} Basic Details : Profile Name * Interface Type *"

        driver.active_form_text = text
        try:
            return await driver.assert_active_surface(SimpleNamespace(), PHASE)
        finally:
            driver.active_form_text = original

    assert asyncio.run(run("Edit Transport Profile"))["fatal"] == []
    assert asyncio.run(run("Clone Transport Profile"))["fatal"] == []
    assert asyncio.run(run("Transport Profiles"))["fatal"]


def test_certified_replay_skips_the_per_field_mcp_and_vision_proof_but_not_for_mutations():
    from hip_id_agent.browser_session import BrowserSession

    resolved = []

    class Gate:
        enabled = True

        async def resolve(self, **kw):
            resolved.append(kw["label"])
            return {"pass": True, "status": "proven", "candidate": {}}

    session = BrowserSession.__new__(BrowserSession)
    session.semantic_action_gate = Gate()
    session._portal_mutation_authorization = {}
    session._active_phase_name = PHASE
    session.page = session.playwright_mcp_backend = session.mcp_backend = session.vision_runtime = None
    session.agent_live_view = None
    session.deterministic_replay_active = True
    fill = asyncio.run(session._semantic_action_preflight(action="fill", locator=None, label="Profile Name"))
    assert fill["status"] == "certified_replay_binding" and resolved == []
    asyncio.run(session._semantic_action_preflight(action="click", locator=None, label="Save"))
    assert resolved == ["Save"]  # a committing action is always proven
