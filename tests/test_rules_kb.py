from hip_id_agent.rules_kb import (
    RULES_URL,
    _candidate_rule_deep_profile_urls,
    build_dummy_fill_values,
    extract_rule_records_from_payload,
    normalize_rule_deep_profile,
)


def test_extract_rule_records_from_summary_payload():
    payload = {
        "rules": [
            {
                "ruleId": 77,
                "ruleName": "UHAUL_POASN_RULE",
                "ruleVersion": "1",
                "sourceDocumentTypeId": 10482,
                "targetDocumentTypeId": 10483,
                "ruleConditions": [{"attributeName": "Receiver", "operator": "Equals", "value": "UHAUL"}],
                "ruleActions": [{"actionType": "Mapping Transformer", "mappingIdentifierName": "UHAUL_ASN_MAP", "mappingVersion": "1"}],
            }
        ]
    }
    rows = extract_rule_records_from_payload(payload, source_url="/api/rule/summary")
    assert len(rows) == 1
    row = rows[0]
    assert row["rule_id"] == "77"
    assert row["rule_name"] == "UHAUL_POASN_RULE"
    assert row["source_document_type_id"] == "10482"
    assert row["target_document_type_id"] == "10483"
    assert row["condition_count"] == 1
    assert row["action_count"] == 1
    assert row["actions"][0]["mapping_identifier"] == "UHAUL_ASN_MAP"


def test_normalize_rule_deep_profile_details_payload():
    payload = {
        "ruleDetail": {
            "ruleId": 88,
            "ruleName": "UHAUL_POASN_RULE",
            "ruleVersion": "2",
            "sourceDocumentTypeName": "UHAUL_SRC_DOC",
            "targetDocumentTypeName": "UHAUL_TRGT_DOC",
            "ruleConditions": [{"attributeName": "Sender", "operator": "Contains", "value": "UHAUL"}],
            "ruleActions": [{"actionType": "Mapping Transformer", "mapName": "UHAUL_856_MAP", "mapVersion": "2"}],
        },
        "flowDetail": [{"flowName": "UHAUL_POASN_FLOW"}],
    }
    profile = normalize_rule_deep_profile(payload, source_url="/api/rule/88/details")
    assert profile is not None
    assert profile["rule_id"] == "88"
    assert profile["rule_version"] == "2"
    assert profile["condition_count"] == 1
    assert profile["action_count"] == 1
    assert profile["mapping_identifier"] == "UHAUL_856_MAP"
    assert profile["related_usage"]["flowDetail"]["count"] == 1


def test_candidate_rule_deep_profile_urls_use_rule_endpoint():
    urls = _candidate_rule_deep_profile_urls({"rule_id": "88", "rule_name": "R", "rule_version": "1"}, [])
    assert any("/api/rule/88/details" in u for u in urls)
    assert any("ruleId=88" in u for u in urls)


def test_dummy_fill_rule_values_are_safe():
    vals = build_dummy_fill_values({"rule_name": "REAL_RULE", "mapping_identifier": "REAL_MAP"})
    assert vals["rule_name"].startswith("DUMMY")
    assert vals["mapping_identifier"] == "REAL_MAP"

import pytest

from hip_id_agent.rules_kb import _capture_rule_deep_profiles


class _FakePage:
    async def evaluate(self, *args, **kwargs):
        return {"ok": False, "status": 404, "url": args[1]["url"] if len(args) > 1 and isinstance(args[1], dict) else "", "text": ""}


class _FakeBrowser:
    def __init__(self, events):
        self.network_tab_events = events


@pytest.mark.asyncio
async def test_capture_rule_deep_profiles_reuses_ui_network_detail_payload():
    events = [
        {
            "url": "/inaas-gateway/hipService-svc/api/rule/88/details",
            "method": "GET",
            "response_body_redacted": {
                "ruleDetail": {
                    "ruleId": 88,
                    "ruleName": "UHAUL_POASN_RULE",
                    "ruleVersion": "2",
                    "ruleConditions": [{"attributeName": "Receiver", "operator": "Equals", "value": "UHAUL"}],
                    "ruleActions": [{"actionType": "Mapping Transformer", "mappingIdentifierName": "UHAUL_MAP", "mappingVersion": "2"}],
                }
            },
        }
    ]
    rows, audit, report = await _capture_rule_deep_profiles(
        _FakePage(),
        _FakeBrowser(events),
        [{"rule_name": "UHAUL_POASN_RULE", "rule_version": "2"}],
        [],
        max_profiles=1,
    )
    assert report["deep_profiles_captured"] == 1
    assert rows[0]["rule_id"] == "88"
    assert rows[0]["condition_count"] == 1
    assert rows[0]["action_count"] == 1
    assert rows[0]["mapping_identifier"] == "UHAUL_MAP"
    assert audit[0]["used_existing_network_profile"] is True
