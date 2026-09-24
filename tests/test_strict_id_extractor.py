from hip_id_agent.id_extractor import IDExtractor, StrictIDValidator
from hip_id_agent.models import IDCandidate, NetworkTabEvent


def test_generic_unrelated_id_rejected():
    ev = NetworkTabEvent(
        request_id="r1",
        url="https://developer.dell.com/api/users/search",
        method="GET",
        status=200,
        response_body_redacted={"items": [{"id": "99999", "name": "Random User"}]},
    )
    item, debug = IDExtractor().extract(object_type="partner", query="UHAL", url="https://developer.dell.com/hybrid-integrations/bizlink/partner", html="", body_text="", network_records=[], network_tab_events=[ev])
    assert item is None
    rejected = debug["verified_result"]["rejected_candidates"]
    assert rejected
    assert any("generic id" in " ".join(x.get("rejection_reasons", [])).lower() or "query" in " ".join(x.get("rejection_reasons", [])).lower() for x in rejected)


def test_strong_partner_field_accepted():
    ev = NetworkTabEvent(
        request_id="r2",
        url="https://developer.dell.com/api/partners/search",
        method="GET",
        status=200,
        response_body_redacted={"partnerId": "10483", "partnerName": "UHAL"},
    )
    item, debug = IDExtractor().extract(object_type="partner", query="UHAL", url="https://developer.dell.com/hybrid-integrations/bizlink/partner", html="", body_text="", network_records=[], network_tab_events=[ev])
    assert item is not None
    assert item.object_id == "10483"
    assert item.confidence >= 0.90


def test_generic_id_matching_endpoint_and_name_accepted():
    ev = NetworkTabEvent(
        request_id="r3",
        url="https://developer.dell.com/api/partners/search",
        method="GET",
        status=200,
        response_body_redacted={"id": "10483", "name": "UHAL"},
    )
    item, debug = IDExtractor().extract(object_type="partner", query="UHAL", url="https://developer.dell.com/hybrid-integrations/bizlink/partner", html="", body_text="", network_records=[], network_tab_events=[ev])
    assert item is not None
    assert item.object_id == "10483"


def test_generic_id_matching_endpoint_wrong_name_not_auto_saved():
    ev = NetworkTabEvent(
        request_id="r4",
        url="https://developer.dell.com/api/partners/search",
        method="GET",
        status=200,
        response_body_redacted={"id": "10483", "name": "WRONG"},
    )
    item, debug = IDExtractor().extract(object_type="partner", query="UHAL", url="https://developer.dell.com/hybrid-integrations/bizlink/partner", html="", body_text="", network_records=[], network_tab_events=[ev])
    assert item is None
    assert debug["verified_result"]["accepted"] is False


def test_conflicting_candidates_ambiguous():
    candidates = [
        IDCandidate("partner", "11111", "UHAL", "network", endpoint_url="/api/partners/search", field_path="partnerId", surrounding_text="UHAL", confidence=0.1),
        IDCandidate("partner", "22222", "UHAL", "dom_detail", source_url="/partners/22222", endpoint_url="/partners/22222", field_path="partnerId", surrounding_text="UHAL", confidence=0.1),
    ]
    result = StrictIDValidator().choose(candidates, "UHAL", "partner")
    assert result.accepted is False
    assert result.ambiguous is True


def test_system_id_not_accepted_as_partner_id():
    ev = NetworkTabEvent(
        request_id="r5",
        url="https://developer.dell.com/api/systems/search",
        method="GET",
        status=200,
        response_body_redacted={"systemId": "SYS123", "systemName": "UHAL"},
    )
    item, debug = IDExtractor().extract(object_type="partner", query="UHAL", url="https://developer.dell.com/hybrid-integrations/bizlink/partner", html="", body_text="", network_records=[], network_tab_events=[ev])
    assert item is None


def test_partner_id_not_accepted_as_system_id():
    ev = NetworkTabEvent(
        request_id="r6",
        url="https://developer.dell.com/api/partners/search",
        method="GET",
        status=200,
        response_body_redacted={"partnerId": "P123", "partnerName": "UHAL"},
    )
    item, debug = IDExtractor().extract(object_type="system", query="UHAL", url="https://developer.dell.com/hybrid-integrations/bizlink/system", html="", body_text="", network_records=[], network_tab_events=[ev])
    assert item is None
