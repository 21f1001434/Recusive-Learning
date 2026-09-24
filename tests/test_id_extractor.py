from hip_id_agent.id_extractor import IDExtractor


def test_extract_partner_id_from_table():
    html = """
    <table>
      <tr><th>Partner Name</th><th>Partner ID</th></tr>
      <tr><td>UHAL</td><td>12345</td></tr>
    </table>
    """
    item, debug = IDExtractor().extract(
        object_type="partner",
        query="UHAL",
        url="https://hip/partners",
        html=html,
        body_text="UHAL Partner ID 12345",
        network_records=[],
    )
    assert item is not None
    assert item.object_id == "12345"
    assert item.source in {"html_table", "text_key_value"}


def test_extract_system_id_from_text():
    item, debug = IDExtractor().extract(
        object_type="system",
        query="UHAL-SFTP",
        url="https://hip/systems/8888",
        html="<html><body>System Name UHAL-SFTP System ID: 8888</body></html>",
        body_text="System Name UHAL-SFTP\nSystem ID: 8888",
        network_records=[],
    )
    assert item is not None
    assert item.object_id == "8888"

from hip_id_agent.models import NetworkTabEvent


def test_extract_partner_id_from_network_tab_event():
    events = [NetworkTabEvent(
        request_id="1",
        url="https://developer.dell.com/api/partner/search",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted={"items": [{"partnerName": "UHAL", "partnerId": "P12345"}]},
    )]
    item, debug = IDExtractor().extract(
        object_type="partner",
        query="UHAL",
        url="https://developer.dell.com/hybrid-integrations/bizlink/partner",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert item is not None
    assert item.object_id == "P12345"
    assert item.source == "network_tab_json"


def test_extract_system_domain_id_from_network_tab_event():
    events = [NetworkTabEvent(
        request_id="2",
        url="https://developer.dell.com/api/system/search",
        method="GET",
        status=200,
        resource_type="XHR",
        response_body_redacted={"content": [{"domainName": "UHAL-SFTP", "domainId": "98765"}]},
    )]
    item, debug = IDExtractor().extract(
        object_type="system",
        query="UHAL-SFTP",
        url="https://developer.dell.com/hybrid-integrations/bizlink/system",
        html="",
        body_text="",
        network_records=[],
        network_tab_events=events,
    )
    assert item is not None
    assert item.object_id == "98765"
