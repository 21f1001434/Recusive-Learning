import json
from hip_id_agent.datamap_kb import extract_data_map_seed, build_dummy_fill_values, guess_field_key, build_previous_interaction_values


def test_extract_data_map_seed_from_objects():
    data = {"objects": {"data_map": {"map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "status": "Enable", "contivo_version": "6.7"}}}
    seed = extract_data_map_seed(data)
    assert seed["map_identifier"] == "DELLCoXMLASNXX08C_U-HAUL"
    assert seed["status"] == "Enable"
    assert seed["contivo_version"] == "6.7"


def test_dummy_values_are_marked_dummy_without_losing_shape():
    seed = {"map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_name": "DELLCoXMLASNXX08C", "map_identifier_version": "1"}
    dummy = build_dummy_fill_values(seed)
    assert dummy["map_identifier"].startswith("DUMMY_")
    assert dummy["map_name"].startswith("DUMMY_")
    assert dummy["map_identifier_version"] == "1"


def test_guess_field_key_recognizes_core_datamap_fields():
    assert guess_field_key("Map Identifier", {}) == "map_identifier"
    assert guess_field_key("Map Identifier Version", {}) == "map_identifier_version"
    assert guess_field_key("Map Class", {}) == "map_class"
    assert guess_field_key("Map Data File", {}) == "map_data_file"


def test_previous_values_include_known_map_id_and_related_names():
    data = {"objects": {"data_map": {"map_identifier": "M"}, "source_document_type": {"name": "SRC"}, "target_document_type": {"name": "TGT"}, "rule": {"name": "R", "actions": {"mapping_identifier_name_version": "M(1.0)"}}}}
    out = build_previous_interaction_values(data, known_map_id="1670")
    assert out["known_ids"]["map_id"] == "1670"
    assert out["related_values"]["source_document_type_name"] == "SRC"
    assert out["related_values"]["mapping_identifier_name_version"] == "M(1.0)"

from hip_id_agent.datamap_kb import (
    extract_datamap_record,
    extract_datamap_records_from_payload,
    collect_datamap_api_interactions,
)


def test_extract_datamap_record_accepts_typed_datamap_fields():
    row = {
        "id": 1670,
        "mapIdentifier": "DELLCoXMLASNXX08C_U-HAUL",
        "mapIdentifierVersion": "1",
        "mapName": "DELLCoXMLASNXX08C",
        "mapClass": "Transform_DELLCoXMLASNXX08C",
        "contivoVersion": "6.7",
        "mapDataFile": "Transform_DELLCoXMLASNXX08C.jar",
        "status": "Enable",
    }
    out = extract_datamap_record(row, source_url="https://developer.dell.com/securelink/datamaps")
    assert out["map_id"] == "1670"
    assert out["map_identifier"] == "DELLCoXMLASNXX08C_U-HAUL"
    assert out["map_data_file"] == "Transform_DELLCoXMLASNXX08C.jar"


def test_extract_datamap_payload_from_wrapped_content():
    payload = {"content": [{"mapId": "10", "mapIdentifier": "A", "version": "1"}, {"mapId": "11", "mapIdentifier": "B", "version": "1"}]}
    rows = extract_datamap_records_from_payload(payload, source_url="https://x/datamaps?page=0")
    assert [r["map_id"] for r in rows] == ["10", "11"]


def test_collect_datamap_api_interactions_records_triggered_api_and_values():
    class Ev:
        def __init__(self):
            self.url = "https://developer.dell.com/hipSecureLinkService-svc/api/datamaps?page=0"
            self.method = "GET"
            self.status = 200
            self.mime_type = "application/json"
            self.resource_type = "fetch"
            self.request_headers = {"x-requester-id": "user@dell.com", "cookie": "secret"}
            self.request_body_redacted = None
            self.response_body_redacted = {"items": [{"mapId": 1670, "mapIdentifier": "DELLCoXMLASNXX08C_U-HAUL", "mapName": "DELLCoXMLASNXX08C"}]}
            self.response_body_text_redacted = None
            self.timestamp = "now"
            self.page_context = "https://developer.dell.com/hybrid-integrations/securelink/datamaps"
    interactions, rows = collect_datamap_api_interactions([Ev()], stage_label="listing")
    assert len(interactions) == 1
    assert interactions[0]["datamap_rows_extracted"] == 1
    assert rows[0]["map_id"] == "1670"
    assert "cookie" not in interactions[0]["request_headers_compact"]

from pathlib import Path
from hip_id_agent.datamap_kb import build_datamap_api_flow_knowledge_graph, _write_datamap_api_flow_knowledge_graph, _write_datamap_api_flow_knowledge_graph_safe


def test_datamap_api_flow_knowledge_graph_links_api_records_and_form_fields(tmp_path: Path):
    kb = {
        "run_id": "DM-KB-TEST",
        "captured_at": "now",
        "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps",
        "safety": {"save_clicked": False},
        "previous_interaction_values": {"data_map_values_to_fill": {"map_identifier": "DELLCoXMLASNXX08C_U-HAUL"}, "known_ids": {"map_id": "1670"}},
        "datamap_api_interactions": [
            {
                "stage": "datamap_listing_load",
                "method": "GET",
                "url": "https://developer.dell.com/hipSecureLinkService-svc/api/datamaps?page=0",
                "status": 200,
                "response_shape": {"type": "dict", "row_count": 1, "sample_keys": ["mapId", "mapIdentifier"]},
                "datamap_rows_extracted": 1,
                "sample_datamap_rows": [{"map_id": "1670", "map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_name": "DELLCoXMLASNXX08C"}],
            }
        ],
        "old_datamaps_inventory": [{"map_id": "1670", "map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_name": "DELLCoXMLASNXX08C"}],
        "pagination_replay_audit": [{"url": "https://developer.dell.com/hipSecureLinkService-svc/api/datamaps?page=1", "status": 200, "ok": True}],
        "form_controls": [{"label": "Map Identifier", "selector": "#mapIdentifier", "tag": "input", "type": "text", "required": True, "mapped_data_map_key": "map_identifier", "recommended_value": "DUMMY_DELLCoXMLASNXX08C_U-HAUL_KB"}],
        "required_fields": [{"label": "Map Identifier"}],
        "dropdowns": [{"label": "Status", "selector": "#status", "options": [{"text": "Enable"}], "dom_event": "click->listbox option->change"}],
        "dummy_fill_attempts": [{"field": "map_identifier", "selector": "#mapIdentifier", "filled": True}],
    }
    graph = build_datamap_api_flow_knowledge_graph(kb)
    assert graph["summary"]["api_interactions"] == 1
    assert graph["summary"]["old_datamaps"] == 1
    types = {n["type"] for n in graph["nodes"]}
    assert "API_INTERACTION" in types
    assert "DATAMAP_RECORD" in types
    assert "FORM_FIELD" in types
    assert "DROPDOWN" in types
    rels = {e["relation"] for e in graph["edges"]}
    assert "PAGE_TRIGGERED_API" in rels
    assert "API_RETURNED_DATAMAP_RECORD" in rels
    assert "ADD_FORM_CONTAINED_FIELD" in rels


def test_datamap_api_flow_kg_writer_creates_uploadable_files(tmp_path: Path):
    kb = {
        "run_id": "DM-KB-TEST",
        "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps",
        "safety": {"save_clicked": False},
        "datamap_api_interactions": [],
        "old_datamaps_inventory": [],
        "form_controls": [],
        "required_fields": [],
        "dropdowns": [],
        "dummy_fill_attempts": [],
    }
    files = _write_datamap_api_flow_knowledge_graph(tmp_path, kb)
    assert Path(files["datamap_api_flow_kg_json"]).exists()
    assert Path(files["datamap_api_flow_kg_html"]).exists()
    assert Path(files["datamap_api_flow_kg_mermaid"]).read_text(encoding="utf-8").startswith("flowchart TD")


def test_datamap_api_flow_knowledge_graph_accepts_dom_label_in_dummy_fill():
    kb = {
        "run_id": "DM-KB-LABEL-TEST",
        "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps",
        "safety": {"save_clicked": False},
        "datamap_api_interactions": [],
        "old_datamaps_inventory": [],
        "form_controls": [],
        "dropdowns": [],
        "dummy_fill_attempts": [
            {
                "field": "map_identifier",
                "selector": "#mapIdentifier",
                "label": "Map Identifier",
                "type": "text",
                "filled": True,
            }
        ],
    }
    graph = build_datamap_api_flow_knowledge_graph(kb)
    dummy_nodes = [n for n in graph["nodes"] if n["type"] == "DUMMY_FILL"]
    assert dummy_nodes
    assert dummy_nodes[0]["properties"]["label"] == "Map Identifier"

from hip_id_agent.datamap_kb import (
    _apply_known_map_ids,
    _candidate_datamap_detail_urls,
    _merge_datamap_records,
)


def test_known_map_id_is_applied_to_existing_inventory_record():
    records = [{"map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_name": "DELLCoXMLASNXX08C", "map_id": ""}]
    prev = {"data_map_values_to_fill": {"map_identifier": "DELLCoXMLASNXX08C_U-HAUL"}, "known_ids": {"map_id": "1670"}}
    out = _apply_known_map_ids(records, prev)
    assert out[0]["map_id"] == "1670"
    assert out[0]["map_id_source"] == "known_prior_context"


def test_datamap_detail_candidate_urls_are_read_only_and_identifier_based():
    row = {"map_identifier": "DELLCoXMLASNXX08C_U-HAUL", "map_identifier_version": "1.0", "map_name": "DELLCoXMLASNXX08C", "available_environments": "DEV"}
    interactions = [{"method": "GET", "url": "https://developer.dell.com/inaas-gateway/hipService-svc/api/mac-map/summary"}]
    urls = _candidate_datamap_detail_urls(row, interactions)
    assert urls
    assert all("mapIdentifier=" in u or "DELLCoXMLASNXX08C_U-HAUL" in u for u in urls)
    assert not any("save" in u.lower() or "create" in u.lower() for u in urls)


def test_merge_datamap_records_prefers_detail_map_id():
    base = {"map_identifier": "A", "map_identifier_version": "1.0", "map_name": "M", "map_id": ""}
    detail = {"map_identifier": "A", "map_identifier_version": "1.0", "map_name": "M", "map_id": "99", "source": "api_detail_enrichment"}
    out = _merge_datamap_records([base, detail])
    assert len(out) == 1
    assert out[0]["map_id"] == "99"


def test_datamap_api_flow_kg_accepts_runtime_records_with_existing_order():
    kb = {
        "run_id": "DM-KB-ORDER-COLLISION",
        "url": "https://developer.dell.com/hybrid-integrations/securelink/datamaps",
        "safety": {"save_clicked": False},
        "datamap_api_interactions": [],
        "old_datamaps_inventory": [],
        "pagination_replay_audit": [{"url": "https://example.test?page=1", "status": 200, "order": 77}],
        "detail_enrichment_audit": [{
            "map_identifier": "MAP",
            "attempts": [{"url": "https://example.test/detail", "status": 200, "order": 88}],
        }],
        "form_controls": [],
        "dropdowns": [],
        "dummy_fill_attempts": [{
            "field": "map_identifier",
            "selector": "#mapIdentifier",
            "filled": True,
            "order": 4,
        }],
    }
    graph = build_datamap_api_flow_knowledge_graph(kb)
    dummy = next(n for n in graph["nodes"] if n["type"] == "DUMMY_FILL")
    assert dummy["properties"]["order"] == 4
    assert dummy["properties"]["graph_order"] == 1
    pagination = next(n for n in graph["nodes"] if n["type"] == "PAGINATION_REPLAY")
    assert pagination["properties"]["order"] == 77
    assert pagination["properties"]["graph_order"] == 1


def test_datamap_api_flow_kg_safe_writer_is_non_blocking(tmp_path: Path, monkeypatch):
    import hip_id_agent.datamap_kb as module

    def boom(_kb):
        raise TypeError("synthetic duplicate property")

    monkeypatch.setattr(module, "build_datamap_api_flow_knowledge_graph", boom)
    files = _write_datamap_api_flow_knowledge_graph_safe(tmp_path, {"run_id": "DM-KB-SAFE"})
    status_path = Path(files["datamap_api_flow_kg_export_status"])
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "warning"
    assert status["non_blocking"] is True
    assert status["error_type"] == "TypeError"
