from hip_id_agent.api_client import HipAPIClient
from hip_id_agent.config import APIConfig


def test_enrich_payload_recursively():
    client = HipAPIClient(APIConfig(account_id="A1"))
    payload = {
        "transportProfileDetails": {"partnerId": "${partner_id}", "domainId": ""},
        "nested": [{"system_id": "{{system_id}}", "account_id": "TBD"}],
    }
    out = client.enrich_payload(payload, partner_id="P1", system_id="S1", account_id="A1")
    assert out["transportProfileDetails"]["partnerId"] == "P1"
    assert out["transportProfileDetails"]["domainId"] == "S1"
    assert out["nested"][0]["system_id"] == "S1"
    assert out["nested"][0]["account_id"] == "A1"
