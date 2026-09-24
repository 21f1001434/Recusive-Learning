from pathlib import Path

from hip_id_agent.id_extractor import IDExtractor
from hip_id_agent.memory import HipMemory
from hip_id_agent.models import ExtractedID, NetworkTabEvent


def test_accounts_endpoint_is_not_accepted_as_partner_id():
    events = [NetworkTabEvent(
        request_id="accounts-1",
        url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted=[{
            "id": "5a51c5ac-add2-4350-a08f-e21deadbeef0",
            "accountName": "AS2TEST",
            "description": "AS2TEST account",
            "domains": ["as2test.com"],
        }],
        page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
    )]
    best, debug = IDExtractor().extract(
        object_type="partner",
        query="AS2TEST",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert best is None
    assert any("endpoint kind account" in " ".join(c.get("rejection_reasons", [])) for c in debug["rejected_candidates"])


def test_accounts_endpoint_is_saved_as_account_id():
    events = [NetworkTabEvent(
        request_id="accounts-2",
        url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted=[{
            "id": "5a51c5ac-add2-4350-a08f-e21deadbeef0",
            "accountName": "AS2TEST",
            "description": "AS2TEST account",
            "domains": ["as2test.com"],
        }],
        page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
    )]
    best, debug = IDExtractor().extract(
        object_type="account",
        query="AS2TEST",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert best is not None
    assert best.object_id == "5a51c5ac-add2-4350-a08f-e21deadbeef0"
    assert best.name == "AS2TEST"


def test_partners_endpoint_exact_name_wins_and_fuzzy_partner_is_not_ambiguous():
    events = [NetworkTabEvent(
        request_id="partners-1",
        url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/partners",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted=[
            {"id": "11111111-1111-1111-1111-111111111111", "name": "SCG_AS2_TEST_IN", "description": "AS2 testing inbound"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "AS2TEST", "description": "AS2TEST", "partnerIdentifierValue": "AMAZON"},
        ],
        page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
    )]
    best, debug = IDExtractor().extract(
        object_type="partner",
        query="AS2TEST",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert best is not None
    assert best.object_id == "22222222-2222-2222-2222-222222222222"
    assert not debug["verified_result"].get("ambiguous")


def test_bulk_discovery_separates_account_and_partner_endpoints():
    events = [
        NetworkTabEvent(
            request_id="accounts-3",
            url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
            method="GET",
            status=200,
            resource_type="XHR",
            response_body_redacted=[{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "accountName": "AS2TEST"}],
        ),
        NetworkTabEvent(
            request_id="partners-3",
            url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/partners",
            method="GET",
            status=200,
            resource_type="XHR",
            response_body_redacted=[{"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "name": "AS2TEST"}],
        ),
    ]
    ext = IDExtractor()
    partners = ext.extract_all_from_network("partner", events)
    accounts = ext.extract_all_from_network("account", events)
    assert [p.object_id for p in partners] == ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]
    assert [a.object_id for a in accounts] == ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]


def test_memory_supports_account_store(tmp_path: Path):
    mem = HipMemory(tmp_path)
    mem.save_entity("account", ExtractedID("account", "AS2TEST", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "AS2TEST", "network", 0.9, {}))
    assert mem.find_entity("account", "AS2TEST")["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert mem.find_entity("partner", "AS2TEST") is None


def test_nested_account_partners_endpoint_is_partner_payload_not_account_payload():
    account_id = "5a51c5ac-add2-4350-a08f-e21dd8d90940"
    partner_id = "ff0341d2-018b-4489-8d23-669000000001"
    events = [
        NetworkTabEvent(
            request_id="accounts-gmail",
            url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
            method="GET",
            status=200,
            resource_type="XHR",
            response_body_redacted=[{"id": account_id, "accountName": "Gmail Account", "description": "Gmail Account", "domains": ["gmail.com"]}],
            page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        ),
        NetworkTabEvent(
            request_id="gmail-partners",
            url=f"https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts/{account_id}/partners",
            method="GET",
            status=200,
            resource_type="XHR",
            response_body_redacted=[{"id": partner_id, "name": "AS2TEST", "description": "AS2TEST", "partnerIdentifier": "DUNS Number", "partnerIdentifierValue": "AMAZONJP"}],
            page_context="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        ),
    ]

    partner, partner_debug = IDExtractor().extract(
        object_type="partner",
        query="AS2TEST",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert partner is not None
    assert partner.object_id == partner_id
    assert partner.name == "AS2TEST"
    assert partner.evidence["endpoint_url"].endswith(f"/accounts/{account_id}/partners")

    account_for_as2, account_debug = IDExtractor().extract(
        object_type="account",
        query="AS2TEST",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert account_for_as2 is None
    assert any("endpoint kind partner" in " ".join(c.get("rejection_reasons", [])) for c in account_debug["rejected_candidates"])


def test_bulk_discovery_does_not_save_nested_partner_rows_as_accounts():
    account_id = "5a51c5ac-add2-4350-a08f-e21dd8d90940"
    partner_id = "ff0341d2-018b-4489-8d23-669000000001"
    events = [
        NetworkTabEvent(
            request_id="accounts-gmail-bulk",
            url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
            method="GET",
            status=200,
            response_body_redacted=[{"id": account_id, "accountName": "Gmail Account", "description": "Gmail Account"}],
        ),
        NetworkTabEvent(
            request_id="account-partners-bulk",
            url=f"https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts/{account_id}/partners",
            method="GET",
            status=200,
            response_body_redacted=[{"id": partner_id, "name": "AS2TEST", "description": "AS2TEST"}],
        ),
    ]
    ext = IDExtractor()
    accounts = ext.extract_all_from_network("account", events)
    partners = ext.extract_all_from_network("partner", events)
    assert [a.object_id for a in accounts] == [account_id]
    assert [p.object_id for p in partners] == [partner_id]


def test_nested_account_domains_endpoint_is_system_payload_not_account_payload():
    account_id = "5a51c5ac-add2-4350-a08f-e21dd8d90940"
    system_id = "7f8fd919-c696-4b01-9ad8-395000000001"
    events = [
        NetworkTabEvent(
            request_id="accounts-cx",
            url="https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts",
            method="GET",
            status=200,
            response_body_redacted=[{"id": account_id, "accountName": "Customer Experience (CX)", "description": "Customer Experience"}],
        ),
        NetworkTabEvent(
            request_id="cx-domains",
            url=f"https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts/{account_id}/domains",
            method="GET",
            status=200,
            response_body_redacted=[{"id": system_id, "domainName": "AIC-DCE", "description": "AIC-DCE domain"}],
            page_context="https://developer.dell.com/hybrid-integrations/bizlink/system",
        ),
    ]

    system, system_debug = IDExtractor().extract(
        object_type="system",
        query="AIC-DCE",
        url="https://developer.dell.com/hybrid-integrations/bizlink/system",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert system is not None
    assert system.object_id == system_id
    assert system.name == "AIC-DCE"
    assert system.evidence["endpoint_url"].endswith(f"/accounts/{account_id}/domains")

    account_for_system, account_debug = IDExtractor().extract(
        object_type="account",
        query="AIC-DCE",
        url="https://developer.dell.com/hybrid-integrations/bizlink/system",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert account_for_system is None
    assert any("endpoint kind system" in " ".join(c.get("rejection_reasons", [])) for c in account_debug["rejected_candidates"])
