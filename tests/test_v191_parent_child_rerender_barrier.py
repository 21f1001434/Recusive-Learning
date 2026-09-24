import inspect

import pytest

import hip_id_agent.stateful_form_runtime as sfr
from hip_id_agent.form_interaction_policy import DEFAULT_FORM_INTERACTION_POLICY


class FakePage:
    async def wait_for_timeout(self, _ms):
        return None


def _graph():
    parent = {
        "node_id": "parent",
        "field_key": "derived_from",
        "action": "select_single",
        "expected_value": "ELEMENT_IN_PAYLOAD",
        "required": True,
    }
    child = {
        "node_id": "child",
        "field_key": "expression",
        "action": "fill_text",
        "expected_value": "/x",
        "required": True,
        "depends_on": ["parent"],
    }
    return {"nodes": [parent, child]}, parent, child


@pytest.mark.asyncio
async def test_generation_barrier_rebinds_parent_after_angular_selector_replacement(monkeypatch):
    graph, parent, _child = _graph()
    captures = [
        [{"node_id": "parent", "selector": "#old", "value": "ELEMENT_IN_PAYLOAD", "semantic_key": "derived_from"}],
        [
            {"node_id": "parent", "selector": "#new", "value": "ELEMENT_IN_PAYLOAD", "semantic_key": "derived_from"},
            {"node_id": "child", "selector": "#child-new", "value": "", "semantic_key": "expression"},
        ],
        [
            {"node_id": "parent", "selector": "#new", "value": "ELEMENT_IN_PAYLOAD", "semantic_key": "derived_from"},
            {"node_id": "child", "selector": "#child-new", "value": "", "semantic_key": "expression"},
        ],
    ]

    async def fake_capture(_page):
        return captures.pop(0) if captures else [
            {"node_id": "parent", "selector": "#new", "value": "ELEMENT_IN_PAYLOAD", "semantic_key": "derived_from"},
            {"node_id": "child", "selector": "#child-new", "value": "", "semantic_key": "expression"},
        ]

    def fake_resolver(controls, node):
        control = next((c for c in controls if c.get("node_id") == node.get("node_id")), None)
        return {"resolved": bool(control), "control": control, "selected_identity": (control or {}).get("selector")}

    monkeypatch.setattr(sfr, "capture_document_type_controls", fake_capture)
    monkeypatch.setattr(sfr, "resolve_document_type_control_diagnostics", fake_resolver)
    monkeypatch.setattr(sfr, "_value_equal", lambda node, control: control.get("value") == node.get("expected_value"))
    monkeypatch.setattr(sfr, "eligible_child_nodes", lambda graph, parent_id, status: [graph["nodes"][1]])

    result = await sfr._wait_for_post_commit_generation_barrier(
        FakePage(), graph, parent, phase="source_document_type",
        node_status={},
        profile={"post_commit_generation_timeout_ms": 1000, "post_commit_generation_stable_samples": 2, "poll_interval_ms": 1},
        selector_before="#old", document_type=True,
    )
    assert result["pass"] is True
    assert result["selector_replaced"] is True
    assert result["selector_after"] == "#new"
    assert any(x["node_id"] == "child" and x["selector"] == "#child-new" for x in result["generation"]["bindings"])


@pytest.mark.asyncio
async def test_generation_barrier_fails_closed_when_parent_commit_is_lost(monkeypatch):
    graph, parent, _child = _graph()

    async def fake_capture(_page):
        return [{"node_id": "parent", "selector": "#new", "value": "", "semantic_key": "derived_from"}]

    def fake_resolver(controls, node):
        control = next((c for c in controls if c.get("node_id") == node.get("node_id")), None)
        return {"resolved": bool(control), "control": control}

    monkeypatch.setattr(sfr, "capture_document_type_controls", fake_capture)
    monkeypatch.setattr(sfr, "resolve_document_type_control_diagnostics", fake_resolver)
    monkeypatch.setattr(sfr, "_value_equal", lambda node, control: control.get("value") == node.get("expected_value"))
    monkeypatch.setattr(sfr, "eligible_child_nodes", lambda *_args, **_kwargs: [])

    result = await sfr._wait_for_post_commit_generation_barrier(
        FakePage(), graph, parent, phase="source_document_type",
        node_status={},
        profile={"post_commit_generation_timeout_ms": 3, "post_commit_generation_stable_samples": 2, "poll_interval_ms": 1},
        selector_before="#old", document_type=True,
    )
    assert result["pass"] is False
    assert result["parent_exact"] is False
    assert result["reason"] == "HIP_POST_COMMIT_DOM_GENERATION_NOT_STABLE"


@pytest.mark.asyncio
async def test_child_gate_requires_same_rebound_child_binding_for_two_samples(monkeypatch):
    graph, parent, child = _graph()
    captures = [
        [{"node_id": "child", "selector": "#child-a", "semantic_key": "expression"}],
        [{"node_id": "child", "selector": "#child-b", "semantic_key": "expression"}],
        [{"node_id": "child", "selector": "#child-b", "semantic_key": "expression"}],
    ]

    async def fake_capture(_page):
        return captures.pop(0) if captures else [{"node_id": "child", "selector": "#child-b", "semantic_key": "expression"}]

    def fake_resolver(controls, node):
        control = next((c for c in controls if c.get("node_id") == node.get("node_id")), None)
        return {"resolved": bool(control), "control": control}

    monkeypatch.setattr(sfr, "capture_document_type_controls", fake_capture)
    monkeypatch.setattr(sfr, "resolve_document_type_control_diagnostics", fake_resolver)
    monkeypatch.setattr(sfr, "eligible_child_nodes", lambda *_args, **_kwargs: [child])
    monkeypatch.setattr(sfr, "close_open_dropdown", lambda *_args, **_kwargs: _async_none())

    result = await sfr._wait_for_parent_children_visible(
        FakePage(), graph, parent, phase="source_document_type", node_status={},
        profile={"child_visibility_timeout_ms": 1000, "post_commit_generation_stable_samples": 2, "poll_interval_ms": 1},
        document_type=True,
    )
    assert result["pass"] is True
    assert result["samples"] >= 3
    assert result["consecutive_samples"] >= 2
    assert result["children"][0]["binding"]["control"]["selector"] == "#child-b"


async def _async_none():
    return None


def test_execution_profiles_define_post_commit_generation_barrier():
    learning = DEFAULT_FORM_INTERACTION_POLICY["learning_mode"]
    replay = DEFAULT_FORM_INTERACTION_POLICY["validated_fast_replay_mode"]
    assert learning["post_commit_generation_timeout_ms"] >= learning["transaction_timeout_ms"]
    assert learning["post_commit_generation_stable_samples"] >= 3
    assert replay["post_commit_generation_stable_samples"] >= 2


def test_document_type_executor_never_reuses_cached_controls_as_durable_locator():
    source = inspect.getsource(sfr.execute_document_type_state_graph)
    assert "before = await capture_document_type_controls(page)" in source
    assert "before = controls_before or await capture_document_type_controls(page)" not in source
    assert "post_commit_generation_barrier" in source


def test_generic_state_graph_executor_also_recaptures_and_uses_generation_barrier():
    source = inspect.getsource(sfr.execute_phase_state_graph)
    assert "before = await capture_stateful_controls(page, phase)" in source
    assert "before = current_controls or await capture_stateful_controls(page, phase)" not in source
    assert "post_commit_generation_barrier" in source
