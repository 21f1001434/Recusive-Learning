"""V243R13: learned knowledge must survive being written to disk.

``safe_write_json`` masks by default. Before R13 the masker treated agent-owned
keys (``session_id``, ``sessions``, ``task_tokens``) as secrets, so:
* interactive teaching sessions were saved as ``session_id: ***MASKED***`` and
  Finish & learn could never capture the demonstration;
* learned recipes / induced skills / replay policies were reloaded with
  ``task_tokens == "***MASKED***"`` and never matched a differently worded task.
These tests exercise the persisted round trip exactly as the live app does.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hip_id_agent.deterministic_recipe import DeterministicRecipeLibrary
from hip_id_agent.interactive_teaching import InteractiveTeachingStore, capture_pending_interactive_teaching
from hip_id_agent.replay_policy import ReplayPolicyEngine
from hip_id_agent.security import mask_sensitive_data
from hip_id_agent.skill_induction import InducedSkillLibrary

STEPS = [
    {"type": "navigate", "action": "navigate", "label": "Data Maps", "target_route": "/securelink/datamaps"},
    {"type": "semantic_click", "action": "click", "label": "Add", "role": "button"},
    {"type": "fill_from_input", "action": "fill", "label": "Map Name", "input_path": "objects.data_map.map_name", "input_root": "objects.data_map"},
]


def test_agent_owned_keys_are_not_masked_but_credentials_still_are():
    masked = mask_sensitive_data({
        "session_id": "abc123", "sessions": [{"session_id": "x", "token": "t"}],
        "task_tokens": ["create", "data", "map"], "access_token": "t", "cookie": "c",
    })
    assert masked["session_id"] == "abc123"
    assert masked["sessions"] == [{"session_id": "x", "token": "***MASKED***"}]
    assert masked["task_tokens"] == ["create", "data", "map"]
    assert masked["access_token"] == "***MASKED***"
    assert masked["cookie"] == "***MASKED***"


class _FakePage:
    url = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"

    async def evaluate(self, _script):
        return [
            {"kind": "click", "text": "Data Maps", "role": "link", "tag": "a", "url": self.url},
            {"kind": "click", "text": "Add", "role": "button", "tag": "button", "url": self.url},
        ]


class _FakeBrowser:
    page = _FakePage()

    async def collect_dom_event_window(self, _cursor, clear=False):
        return {"events": []}


def test_interactive_teaching_finish_and_learn_captures_from_persisted_session(tmp_path: Path):
    store = InteractiveTeachingStore(tmp_path / "teaching")
    started = store.start(run_id="RUN-1", phase="data_map", task="teach data map")
    store.finish(session_id=started["session_id"])
    on_disk = json.loads((tmp_path / "teaching" / "sessions" / f"{started['session_id']}.json").read_text())
    assert on_disk["session_id"] == started["session_id"]

    # This is the live path: the mission discovers the session from disk.
    captured = asyncio.run(capture_pending_interactive_teaching(store, _FakeBrowser(), run_id="RUN-1", phase="data_map"))
    assert len(captured) == 1 and captured[0]["status"] == "captured"
    validated = store.validate(session_id=captured[0]["session_id"], exact_pass=True, human_pass=True, judge_pass=True, run_id="RUN-1")
    assert validated["validated"] is True


def test_deterministic_recipe_matches_new_wording_after_reload(tmp_path: Path):
    lib = DeterministicRecipeLibrary(tmp_path)
    for run in ("R1", "R2"):
        lib.record_success(task="create data map for uhaul asn", actions=["create"], target_area="data map",
                           input_root="objects.data_map", steps=STEPS, form_blueprints=[], reward=1.0, run_id=run)
    reloaded = DeterministicRecipeLibrary(tmp_path)
    sig = next(iter(reloaded.data["recipes"].values()))["signature"]
    assert isinstance(sig["task_tokens"], list) and "uhaul" in sig["task_tokens"]
    match = reloaded.match(task="create data map for uhaul", actions=["create"], target_area="data map", input_root="objects.data_map")
    assert match["active"] is True, match


def test_induced_skill_matches_after_reload(tmp_path: Path):
    lib = InducedSkillLibrary(tmp_path)
    lib.induce(task="create data map for uhaul asn", actions=["create"], target_area="data map", input_root="objects.data_map",
               workflow_steps=STEPS, form_blueprints=[], page_families=["data_map"], run_id="R1", exact_verified=True)
    reloaded = InducedSkillLibrary(tmp_path)
    matches = reloaded.match("create data map for uhaul", actions=["create"], target_area="data map", input_root="objects.data_map")
    assert matches and isinstance(matches[0]["signature"]["task_tokens"], list)


def test_replay_policy_generalizes_to_new_wording_after_dreaming(tmp_path: Path):
    engine = ReplayPolicyEngine(tmp_path)
    for run in ("R1", "R2", "R3"):
        engine.record_episode(task="create data map for uhaul asn", actions=["create"], target_area="data map",
                              input_root="objects.data_map", steps=STEPS, success=True, run_id=run)
    engine.dream(reason="test")
    reloaded = ReplayPolicyEngine(tmp_path)
    policy = next(iter(reloaded.cache["policies"].values()))
    assert isinstance(policy["signature"]["task_tokens"], list)
    decision = reloaded.decide(task="create data map for uhaul asn today", actions=["create"], target_area="data map", input_root="objects.data_map")
    assert decision.get("semantic_match_score") or decision.get("mode") in {"exploitation", "hybrid"}, decision
