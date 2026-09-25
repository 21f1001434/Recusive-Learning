"""V243R19 with a real browser: operations from input.json on a portal with saved records.

``operations_portal_support.OperationsPortal`` serves a Transport Profile
listing with Edit / Clone and a "More actions" menu (Merge, Deploy); the server
keeps the records, so each test checks what was really saved.  A real
``BrowserSession`` runs ``PortalOperationRunner``; a counting AutoWebGLM
bridge stands in for the per-action model (gpt-oss-120b on the live portal).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.portal_operations import run_portal_operations
from loader_portal_support import patch_navigation, real_session_config
from operations_portal_support import PHASE, OperationsPortal
from phase_replica_support import chromium_path

CONFIRM = "ALLOW HIP MUTATION"


def _sftp(name: str, usage: str, group: str, env: str, post: str) -> Dict[str, Any]:
    return {"profile_name": name, "profile_usage": usage, "deployment_group": group, "interface_type": "SFTP HAFT",
            "interface_environment": env, "post_transfer_action": post}


class CountingBridge:
    """Stands in for the AutoWebGLM primary model: counts per-action decisions
    and takes ``latency`` seconds per decision, like a model round trip."""

    primary_framework = True

    def __init__(self, latency: float = 0.0) -> None:
        self.calls = 0
        self.latency = latency

    async def primary_decide(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        if self.latency:
            await asyncio.sleep(self.latency)
        return {"status": "approved", "framework": "autowebglm_primary", "aligned": True}


async def _run(tmp_path: Path, portal: OperationsPortal, runs: List[List[Dict[str, Any]]], *, allow: bool = True,
               latency: float = 0.0):
    if not chromium_path():  # pragma: no cover
        pytest.skip("Chromium unavailable")
    cfg = real_session_config(tmp_path, loading_seconds=20)
    reports, calls = [], []
    for n, operations in enumerate(runs, start=1):
        session = BrowserSession(cfg, tmp_path / f"run{n}")
        await session.start()
        patch_navigation(session)
        bridge = CountingBridge(latency)
        session.autowebglm_bridge = bridge
        try:
            reports.append(await run_portal_operations(
                cfg, {"operations": operations}, run_dir=tmp_path / f"ops{n}", allow_portal_mutation=allow,
                confirmation=CONFIRM if allow else "", browser=session, listing_urls={PHASE: portal.url},
            ))
        finally:
            await session.close()
        calls.append(bridge.calls)
    return reports, calls


def _skills(tmp_path: Path, phase: str = PHASE) -> Dict[str, Any]:
    return json.loads((tmp_path / "memory" / "portal_skills" / f"{phase}.json").read_text(encoding="utf-8"))


def test_edit_is_learned_proved_committed_then_replayed_without_model_calls(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    first = [{"phase": PHASE, "operation": "edit", "target": "TP_ALPHA", "commit": True,
              "values": _sftp("TP_ALPHA", "Receiver", "pt-receiver-sftphaft-dce-shared", "PROD", "Delete")}]
    second = [{"phase": PHASE, "operation": "edit", "target": "TP_BETA", "commit": True,
               "values": _sftp("TP_BETA", "Sender", "da-sender-sftphaft-dce-shared", "PROD", "Move To Archive")}]
    with OperationsPortal() as portal:
        (learn, replay), (learn_calls, replay_calls) = asyncio.run(_run(tmp_path, portal, [first, second], latency=0.4))
        alpha, beta = portal.find("TP_ALPHA"), portal.find("TP_BETA")

    op = learn["operations"][0]
    assert op["status"] == "committed_and_verified", op
    assert op["fill"]["execution_mode"] == "learned_then_certified_by_replay"
    assert op["fill"]["reopened"] == 1  # a fresh form proved the skill before it was saved
    assert op["commit"]["label"] == "save"
    # Exactly the input.json values were saved; "Delete" is a dropdown value, not a mutation.
    assert {k: alpha[k] for k in ("profileUsage", "deploymentGroup", "interfaceEnvironment", "postTransferAction")} == {
        "profileUsage": "Receiver", "deploymentGroup": "pt-receiver-sftphaft-dce-shared",
        "interfaceEnvironment": "PROD", "postTransferAction": "Delete"}

    op = replay["operations"][0]
    assert op["status"] == "committed_and_verified", op
    assert op["fill"]["execution_mode"] == "deterministic_replay"
    assert (beta["profileUsage"], beta["interfaceEnvironment"], beta["postTransferAction"]) == ("Sender", "PROD", "Move To Archive")
    # The learning run consulted the per-action model for every field; the
    # certified replay only to search, open the row action, Save and check the
    # listing -- never for a form field.
    assert replay_calls <= 6 and learn_calls >= replay_calls + 10, (learn_calls, replay_calls)
    assert op["fill"]["replay_seconds"] < learn["operations"][0]["fill"]["learn_seconds"]
    skill = next(iter(_skills(tmp_path)["skills"].values()))
    assert skill["status"] == "certified" and skill["commit"] == {"label": "save", "verified": 2}


def test_a_new_branch_is_relearned_and_value_dependent_deploy_fields_are_learned(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    operations = [
        {"phase": PHASE, "operation": "edit", "target": "TP_ALPHA", "commit": True,
         "values": _sftp("TP_ALPHA", "Sender", "dce-default-sender", "UAT", "None")},
        # AS2 shows its own section: new fields, so the edit skill is re-learned.
        {"phase": PHASE, "operation": "edit", "target": "TP_BETA", "commit": True,
         "values": {"profile_name": "TP_BETA", "profile_usage": "Receiver", "deployment_group": "dce-default-receiver",
                    "interface_type": "AS2", "as2_identifier": "AS2-UHAUL-01", "signing_algorithm": "SHA1",
                    "post_transfer_action": "Delete"}},
        {"phase": PHASE, "operation": "deploy", "target": "TP_ALPHA", "commit": True,
         "values": {"target_environment": "UAT"}, "expect": ["Deployed UAT"]},
        # PROD reveals Change Ticket and Approver: learned for that value.
        {"phase": PHASE, "operation": "deploy", "target": "TP_BETA", "commit": True,
         "values": {"target_environment": "PROD", "change_ticket": "CHG0012345", "approver": "release-manager"},
         "expect": ["Deployed PROD"]},
    ]
    with OperationsPortal() as portal:
        (report,), _ = asyncio.run(_run(tmp_path, portal, [operations]))
        alpha, beta = portal.find("TP_ALPHA"), portal.find("TP_BETA")
    ops = report["operations"]
    assert [o["status"] for o in ops] == ["committed_and_verified"] * 4, ops
    assert ops[1]["fill"]["skill_reason"] == "new_input_field"
    assert {"as2_identifier", "signing_algorithm"} <= set(ops[1]["fill"]["novelty"][0]["fields"])
    assert ops[1]["fill"]["execution_mode"] == "learned_then_certified_by_replay"
    assert (beta["interfaceType"], beta["as2Identifier"], beta["signingAlgorithm"]) == ("AS2", "AS2-UHAUL-01", "SHA1")
    assert alpha["status"] == "Deployed UAT" and beta["status"] == "Deployed PROD"
    assert beta["deployment"] == {"targetEnvironment": "PROD", "changeTicket": "CHG0012345", "approver": "release-manager"}
    # The PROD deploy was not replayed from the UAT skill: a new branch was learned.
    assert ops[3]["fill"]["skill_plan"] == "learn"
    edit_skills = _skills(tmp_path)
    assert "interface_type" in edit_skills["branch_fields"]
    deploy_skills = _skills(tmp_path, f"universal_{PHASE}_deploy")
    assert sum(1 for s in deploy_skills["skills"].values() if s["status"] == "certified") == 2


def test_clone_and_merge_create_and_combine_records(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "YES")
    operations = [
        {"phase": PHASE, "operation": "clone", "target": "TP_BETA", "commit": True,
         "values": _sftp("TP_BETA_COPY", "Sender", "dce-default-sender", "UAT", "None")},
        {"phase": PHASE, "operation": "merge", "target": "TP_BETA_COPY", "commit": True,
         "values": {"merge_into": "TP_BETA", "keep_source_profile": "No"}, "expect": ["Merged"]},
    ]
    with OperationsPortal() as portal:
        (report,), _ = asyncio.run(_run(tmp_path, portal, [operations]))
        names = [r["profileName"] for r in portal.records]
        beta = portal.find("TP_BETA")
    assert [o["status"] for o in report["operations"]] == ["committed_and_verified"] * 2, report["operations"]
    assert "TP_BETA_COPY" not in names  # merged in and removed ("Keep Source Profile: No")
    assert beta["status"] == "Merged" and beta["merged_from"] == "TP_BETA_COPY"
    clone_post = next(p for p in portal.posts if p["body"].get("mode") == "clone")
    assert clone_post["body"]["values"]["profileName"] == "TP_BETA_COPY"


def test_nothing_is_saved_to_the_portal_without_the_mutation_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HIP_ALLOW_PORTAL_MUTATION", "")
    operations = [
        {"phase": PHASE, "operation": "edit", "target": "TP_ALPHA", "commit": True,
         "values": _sftp("TP_ALPHA", "Receiver", "dce-default-receiver", "PROD", "Delete")},
        {"phase": PHASE, "operation": "deploy", "target": "TP_ALPHA", "commit": True, "values": {"target_environment": "UAT"}},
    ]
    with OperationsPortal() as portal:
        (report,), _ = asyncio.run(_run(tmp_path, portal, [operations], allow=False))
        posts = list(portal.posts)
    edit, deploy = report["operations"]
    # The form was learned, proved and filled -- and left unsaved.
    assert edit["fill"]["pass"] is True and edit["fill"]["skill_status"] == "certified"
    assert edit["status"] == "commit_not_authorized"
    # Opening Deploy is itself a mutation: not even opened.
    assert deploy["status"] == "blocked_mutation_authorization"
    assert posts == []
