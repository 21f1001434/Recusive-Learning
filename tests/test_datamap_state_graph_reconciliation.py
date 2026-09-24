import inspect
import json
from pathlib import Path

import pytest

from hip_id_agent.browser_session import BrowserSession
from hip_id_agent.stateful_form_runtime import (
    _stateful_value_equal,
    compile_data_map_state_graph,
    execute_phase_state_graph,
    resolve_stateful_control,
)


def _payload():
    return {
        "objects": {
            "data_map": {
                "map_identifier": "DELLCoXMLASNXX08C_U-HAUL",
                "map_identifier_version": "1",
                "status": "Enabled",
                "map_name": "DELLCoXMLASNXX08C",
                "map_class": "Transform_DELLCoXMLASNXX08C",
                "contivo_version": "6.7",
                "map_data_file": "Transform_DELLCoXMLASNXX08C.jar",
            }
        }
    }


def _live_controls():
    return [
        {
            "index": 0,
            "selector": "input#dds-form-field-824359860",
            "tag": "input",
            "type": "text",
            "role": "",
            "name": "mapIdentifier",
            "placeholder": "",
            "label": "Map Identifier",
            "section": "Map Reference :",
            "value": "DELLCoXMLASNXX08C_U-HAUL",
            "selected_values": [],
            "checked": False,
            "required": True,
            "disabled": False,
            "semantic_key": "map_identifier",
            "label_occurrence": 0,
        },
        {
            "index": 1,
            "selector": "input#dds-form-field-623019767",
            "tag": "input",
            "type": "text",
            "role": "",
            "name": "mapIdentifierVersion",
            "placeholder": "",
            "label": "Map Identifier Version",
            "section": "Map Reference :",
            "value": "1.0",
            "selected_values": [],
            "checked": False,
            "required": False,
            "disabled": False,
            "semantic_key": "map_identifier_version",
            "label_occurrence": 0,
        },
        {
            "index": 2,
            "selector": "div#dds-switch-status",
            "tag": "div",
            "type": "",
            "role": "switch",
            "name": "",
            "placeholder": "",
            "label": "",
            "section": "Map Reference :",
            "value": "",
            "selected_values": [],
            "checked": True,
            "required": False,
            "disabled": False,
            "semantic_key": "",
            "label_occurrence": 0,
        },
        {
            "index": 3,
            "selector": "input#dds-form-field-map-name",
            "tag": "input",
            "type": "text",
            "role": "",
            "name": "mapName",
            "placeholder": "",
            "label": "Map Name",
            "section": "Mapping Details :",
            "value": "DELLCoXMLASNXX08C",
            "selected_values": [],
            "checked": False,
            "required": True,
            "disabled": False,
            "semantic_key": "map_name",
            "label_occurrence": 0,
        },
        {
            "index": 4,
            "selector": "input#dds-form-field-map-class",
            "tag": "input",
            "type": "text",
            "role": "",
            "name": "mapClass",
            "placeholder": "",
            "label": "Map Class",
            "section": "Mapping Details :",
            "value": "Transform_DELLCoXMLASNXX08C",
            "selected_values": [],
            "checked": False,
            "required": True,
            "disabled": False,
            "semantic_key": "map_class",
            "label_occurrence": 0,
        },
        {
            "index": 5,
            "selector": "input#dds-form-field-contivo",
            "tag": "input",
            "type": "text",
            "role": "combobox",
            "name": "",
            "placeholder": "",
            "label": "Contivo version",
            "section": "Mapping Details :",
            "value": "6.7",
            "selected_values": [],
            "checked": False,
            "required": True,
            "disabled": False,
            "semantic_key": "contivo_version",
            "label_occurrence": 0,
        },
        {
            "index": 6,
            "selector": "input#map-data",
            "tag": "input",
            "type": "file",
            "role": "",
            "name": "mapData",
            "placeholder": "",
            "label": "mapData",
            "section": "Mapping Details :",
            "value": r"C:\fakepath\Transform_DELLCoXMLASNXX08C.jar",
            "selected_values": [],
            "checked": False,
            "required": True,
            "disabled": False,
            "semantic_key": "mapdata",
            "label_occurrence": 0,
        },
        # A second unrelated switch must never satisfy Data Map status.
        {
            "index": 7,
            "selector": "div#dds-switch-cross-ref",
            "tag": "div",
            "type": "",
            "role": "switch",
            "name": "",
            "placeholder": "",
            "label": "",
            "section": "Cross Reference Table Details :",
            "value": "",
            "selected_values": [],
            "checked": False,
            "required": False,
            "disabled": False,
            "semantic_key": "",
            "label_occurrence": 0,
        },
    ]


def test_datamap_graph_resolves_live_fieldset_controls_exactly():
    graph = compile_data_map_state_graph(_payload(), "data_map")
    controls = _live_controls()
    for node in graph["nodes"]:
        if node["action"] == "upload_file":
            continue
        control = resolve_stateful_control(controls, node)
        assert control is not None, node["field_key"]
        assert _stateful_value_equal(node, control), node["field_key"]


def test_datamap_status_resolves_map_reference_switch_not_cross_reference_switch():
    graph = compile_data_map_state_graph(_payload(), "data_map")
    status = next(n for n in graph["nodes"] if n["field_key"] == "status")
    resolved = resolve_stateful_control(_live_controls(), status)
    assert resolved is not None
    assert resolved["selector"] == "div#dds-switch-status"
    assert _stateful_value_equal(status, resolved) is True


def test_generic_create_surface_does_not_penalize_nested_fieldset():
    node = {
        "section": "Create Transport Profile",
        "field_key": "profile_name",
        "action": "fill_text",
        "expected_value": "TP-1",
        "semantic_locator": {
            "labels": ["Transport Profile Name", "Name"],
            "names": [],
            "placeholders": [],
            "roles": [],
            "section_aliases": [],
        },
    }
    control = {
        "index": 0,
        "section": "Profile Details",
        "label": "Transport Profile Name",
        "name": "",
        "placeholder": "",
        "semantic_key": "transport_profile_name",
        "type": "text",
        "role": "",
        "disabled": False,
        "value": "TP-1",
    }
    assert resolve_stateful_control([control], node) == control


class _FakePage:
    async def evaluate(self, _script, _arg=None):
        return {"event_seq": 0, "mutation_seq": 0}


@pytest.mark.asyncio
async def test_datamap_execution_accepts_already_committed_live_values(monkeypatch):
    import hip_id_agent.stateful_form_runtime as runtime

    controls = _live_controls()

    async def fake_capture(_page, _phase):
        return [dict(x) for x in controls]

    monkeypatch.setattr(runtime, "capture_stateful_controls", fake_capture)
    graph = compile_data_map_state_graph(_payload(), "data_map")
    result = await execute_phase_state_graph(
        _FakePage(),
        graph,
        phase="data_map",
        prior_attempts=[{
            "field": "map_data_file",
            "success": True,
            "uploaded_file_name": "Transform_DELLCoXMLASNXX08C.jar",
            "file_input_selector": "input#map-data",
            "validation": {"blocking": False},
        }],
    )
    assert result["pass"] is True
    assert result["failed_attempts"] == []
    assert all(a["success"] for a in result["attempts"])


@pytest.mark.asyncio
async def test_wait_ready_has_no_undefined_dom_transition_variables(monkeypatch):
    class Locator:
        @property
        def first(self):
            return self

        async def wait_for(self, **_kwargs):
            return None

    class Page:
        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def locator(self, _selector):
            return Locator()

    session = BrowserSession.__new__(BrowserSession)
    session.page = Page()

    class Portal:
        timeout_ms = 50

    class Config:
        portal = Portal()

    session.config = Config()

    async def begin(_kind, _target):
        class Event:
            action_id = "a1"
        return Event()

    async def overlays(**_kwargs):
        return True

    async def finish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(session, "_begin_action", begin)
    monkeypatch.setattr(session, "wait_for_blocking_overlays_gone", overlays)
    monkeypatch.setattr(session, "_finish_action", finish)
    monkeypatch.setattr("hip_id_agent.browser_session.asyncio.sleep", lambda _x: _async_none())

    await BrowserSession.wait_ready(session)


async def _async_none():
    return None
