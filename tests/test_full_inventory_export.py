import json
from hip_id_agent.inventory import InventoryExtractor
from hip_id_agent.models import NetworkTabEvent, ExtractedID
from hip_id_agent.memory import HipMemory


def ev(url, body):
    return NetworkTabEvent(request_id=url, url=url, method="GET", status=200, response_body_redacted=body)


def test_inventory_extractor_separates_accounts_partners_domains_systems():
    events = [
        ev("https://developer.dell.com/hipAuthService-svc/api/authz/accounts", {"items": [{"id": "11111111-1111-1111-1111-111111111111", "accountName": "Gmail Account"}]}),
        ev("https://developer.dell.com/hipAuthService-svc/api/authz/accounts/11111111-1111-1111-1111-111111111111/partners", {"items": [{"id": "22222222-2222-2222-2222-222222222222", "name": "AS2TEST"}]}),
        ev("https://developer.dell.com/hipAuthService-svc/api/authz/accounts/11111111-1111-1111-1111-111111111111/domains", {"items": [{"id": "33333333-3333-3333-3333-333333333333", "domainName": "Customer Experience (CX)"}]}),
        ev("https://developer.dell.com/domain-systems/domains/33333333-3333-3333-3333-333333333333/systems", {"items": [{"id": "44444444-4444-4444-4444-444444444444", "systemName": "AIC - DCE"}]}),
    ]
    out = InventoryExtractor().from_network_events(events)
    assert out["account"][0].name == "Gmail Account"
    assert out["partner"][0].name == "AS2TEST"
    assert out["partner"][0].parent_account_id == "11111111-1111-1111-1111-111111111111"
    assert out["domain"][0].name == "Customer Experience (CX)"
    assert out["domain"][0].parent_account_id == "11111111-1111-1111-1111-111111111111"
    assert out["system"][0].name == "AIC - DCE"
    assert out["system"][0].parent_domain_id == "33333333-3333-3333-3333-333333333333"


def test_memory_supports_domain_inventory(tmp_path):
    mem = HipMemory(tmp_path)
    mem.save_entity("domain", ExtractedID("domain", "Customer Experience (CX)", "33333333-3333-3333-3333-333333333333", "Customer Experience (CX)", "network_inventory", 0.91))
    assert mem.find_entity("domain", "Customer Experience (CX)")["id"] == "33333333-3333-3333-3333-333333333333"
    assert mem.entity_index.read()["domain"]["count"] == 1


def test_inventory_extractor_ignores_user_details_certificates_and_domain_users():
    events = [
        ev("https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/authz-query/user-details", {"id": "11111111-1111-1111-1111-111111111111", "firstName": "Bad"}),
        ev("https://developer.dell.com/inaas-gateway/hipService-svc/api/certificate/partners/certificates", [{"id": "22222222-2222-2222-2222-222222222222", "name": "cert.crt"}]),
        ev("https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/domain-systems/domains/33333333-3333-3333-3333-333333333333/users", [{"id": "44444444-4444-4444-4444-444444444444", "email": "person@example.com"}]),
    ]
    out = InventoryExtractor().from_network_events(events)
    assert out == {"account": [], "partner": [], "domain": [], "system": [], "deployment_group": []}


def test_inventory_extractor_preserves_x_account_id_parent_header():
    event = ev("https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/partners", [{"id": "22222222-2222-2222-2222-222222222222", "name": "AS2TEST"}])
    event.request_headers = {"x-account-id": "11111111-1111-1111-1111-111111111111"}
    out = InventoryExtractor().from_network_events([event])
    assert out["partner"][0].name == "AS2TEST"
    assert out["partner"][0].parent_account_id == "11111111-1111-1111-1111-111111111111"


def test_inventory_system_id_does_not_use_domain_id_as_system_id():
    out = InventoryExtractor().from_payload(
        "system",
        [{"id": 110, "systemName": "ABACUS", "domainId": "33333333-3333-3333-3333-333333333333", "domain": "CX"}],
        "https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/systems-partners/systems",
    )
    assert out[0].object_id == "110"
    assert out[0].parent_domain_id == "33333333-3333-3333-3333-333333333333"
    assert out[0].parent_domain_name == "CX"

import asyncio
from hip_id_agent.config import AppConfig
from hip_id_agent.inventory import PortalInventoryFlow


class DummySession:
    def __init__(self, events=None, page=None):
        self.network_tab_events = events or []
        self.page = page


def test_full_inventory_replays_x_requester_id_from_captured_network(tmp_path):
    event = ev("https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/domain-systems/domains", [])
    event.request_headers = {"x-requester-id": "user@dellteam.com"}
    flow = PortalInventoryFlow(AppConfig(), HipMemory(tmp_path))
    headers = asyncio.run(flow._default_headers(DummySession([event])))
    assert headers["x-requester-id"] == "user@dellteam.com"
    assert headers["Accept"] == "application/json, text/plain, */*"


class RetryPage:
    def __init__(self):
        self.calls = []

    async def evaluate(self, _script, args=None):
        self.calls.append(args or {})
        headers = (args or {}).get("headers") or {}
        if "x-requester-id" in headers:
            return {"ok": True, "status": 200, "url": (args or {}).get("url"), "text": "[{\"id\":\"11111111-1111-1111-1111-111111111111\",\"accountName\":\"Gmail Account\"}]"}
        return {"ok": False, "status": 400, "url": (args or {}).get("url"), "text": "{\"status\":400}"}


def test_fetch_json_uses_discovered_x_requester_id(tmp_path):
    event = ev("https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/domain-systems/domains", [])
    event.request_headers = {"x-requester-id": "user@dellteam.com"}
    page = RetryPage()
    session = DummySession([event], page)
    flow = PortalInventoryFlow(AppConfig(), HipMemory(tmp_path))
    body, meta = asyncio.run(flow._fetch_json(session, "https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts", headers={"content-type": "application/json"}))
    assert meta["status"] == 200
    assert meta["sent_x_requester_id"] is True
    assert body[0]["accountName"] == "Gmail Account"
    assert len(page.calls) == 1

class TransientServerPage:
    def __init__(self):
        self.calls = []

    async def evaluate(self, _script, args=None):
        self.calls.append(args or {})
        if len(self.calls) < 3:
            return {"ok": False, "status": 500, "url": (args or {}).get("url"), "text": "{\"status\":500,\"error\":\"Internal Server Error\"}"}
        return {"ok": True, "status": 200, "url": (args or {}).get("url"), "text": "[{\"id\":\"11111111-1111-1111-1111-111111111111\",\"name\":\"AS2TEST\"}]"}


def test_fetch_json_retries_transient_500_with_same_requester_header(tmp_path):
    event = ev("https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/accounts", [])
    event.request_headers = {"x-requester-id": "user@dellteam.com"}
    page = TransientServerPage()
    session = DummySession([event], page)
    flow = PortalInventoryFlow(AppConfig(), HipMemory(tmp_path))
    body, meta = asyncio.run(flow._fetch_json(session, "https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/partners", headers={"content-type": "application/json"}))
    assert meta["status"] == 200
    assert meta["attempt_count"] == 3
    assert body[0]["name"] == "AS2TEST"
    assert all((call.get("headers") or {}).get("x-requester-id") == "user@dellteam.com" for call in page.calls)


def test_request_failed_flags_non_ok_status(tmp_path):
    flow = PortalInventoryFlow(AppConfig(), HipMemory(tmp_path))
    assert flow._request_failed({"status": 500, "ok": False}) is True
    assert flow._request_failed({"status": 200, "ok": True}) is False

from pathlib import Path
from hip_id_agent.models import RunContext, StageResult, utc_now
from hip_id_agent.report import ReportWriter


def test_report_writer_treats_partial_success_as_non_failed(tmp_path):
    ctx = RunContext(
        run_id="FULL-INVENTORY-TEST",
        customer="FULL-INVENTORY",
        partner_query="",
        system_query="",
        run_dir=tmp_path,
        screenshots_dir=tmp_path / "screenshots",
    )
    ctx.stage_results.append(StageResult(
        stage="export_all_inventory",
        status="partial_success",
        message="Exported inventory with failed_requests audit.",
        started_at=ctx.started_at,
        finished_at=utc_now(),
        warnings=["1 inventory fan-out request still failed."],
        evidence={"failed_request_count": 1},
    ))
    assert ReportWriter(ctx).build_summary()["run_status"] == "partial_success"


def test_explicit_inventory_run_status_wins_over_partial_stage(tmp_path):
    ctx = RunContext(
        run_id="FULL-INVENTORY-TEST",
        customer="FULL-INVENTORY",
        partner_query="",
        system_query="",
        run_dir=tmp_path,
        screenshots_dir=tmp_path / "screenshots",
    )
    ctx.registry["run_status"] = "partial_success"
    ctx.stage_results.append(StageResult(
        stage="export_all_inventory",
        status="partial_success",
        message="Exported inventory with failed_requests audit.",
        started_at=ctx.started_at,
        finished_at=utc_now(),
    ))
    assert ReportWriter(ctx).build_summary()["run_status"] == "partial_success"

from hip_id_agent.inventory import InventoryEntity


def test_inventory_entity_preserves_full_detail_row():
    out = InventoryExtractor().from_payload(
        "partner",
        [{
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "AS2TEST",
            "partnerIdentifier": "DUNS Number",
            "partnerIdentifierValue": "AMAZONJP",
            "businessContactEmail": "business@example.com",
        }],
        "https://developer.dell.com/inaas-gateway/hipAuthService-svc/api/authz/partners",
        parents={"parent_account_id": "11111111-1111-1111-1111-111111111111", "parent_account_name": "Gmail Account"},
    )
    assert out[0].extra["details"]["partnerIdentifierValue"] == "AMAZONJP"
    assert out[0].extra["details"]["businessContactEmail"] == "business@example.com"


def test_inventory_writes_account_partner_and_domain_system_detail_trees(tmp_path):
    flow = PortalInventoryFlow(AppConfig(), HipMemory(tmp_path / "mem"))
    ctx = RunContext(
        run_id="FULL-INVENTORY-TREE",
        customer="FULL-INVENTORY",
        partner_query="",
        system_query="",
        run_dir=tmp_path,
        screenshots_dir=tmp_path / "screenshots",
    )
    account = InventoryEntity(
        "account", "11111111-1111-1111-1111-111111111111", "Gmail Account", "accounts", "account",
        extra={"details": {"id": "11111111-1111-1111-1111-111111111111", "accountName": "Gmail Account", "domains": ["gmail.com"]}},
    )
    partner = InventoryEntity(
        "partner", "22222222-2222-2222-2222-222222222222", "AS2TEST", "partners", "partner",
        parent_account_id=account.object_id, parent_account_name=account.name,
        extra={"details": {"id": "22222222-2222-2222-2222-222222222222", "name": "AS2TEST", "partnerIdentifierValue": "AMAZONJP"}},
    )
    domain = InventoryEntity(
        "domain", "33333333-3333-3333-3333-333333333333", "Customer Experience (CX)", "domains", "domain",
        extra={"details": {"id": "33333333-3333-3333-3333-333333333333", "name": "Customer Experience (CX)", "componentId": "PT281657"}},
    )
    system = InventoryEntity(
        "system", "44444444-4444-4444-4444-444444444444", "AIC - DCE", "systems", "system",
        parent_domain_id=domain.object_id, parent_domain_name=domain.name,
        extra={"details": {"id": "44444444-4444-4444-4444-444444444444", "systemName": "AIC - DCE", "businessContactName": "Sangeetha"}},
    )
    paths = flow._write_inventory_files(ctx, {"account": [account], "partner": [partner], "domain": [domain], "system": [system]})
    assert Path(paths["inventory_accounts_with_partners_details_json"]).exists()
    tree = json.loads(Path(paths["inventory_complete_tree_json"]).read_text())
    assert tree["accounts_with_partners"][0]["partners"][0]["details"]["partnerIdentifierValue"] == "AMAZONJP"
    assert tree["domains_with_systems"][0]["systems"][0]["details"]["businessContactName"] == "Sangeetha"


def test_inventory_extracts_deployment_group_as_first_class_tp_id_candidate():
    out = InventoryExtractor().from_network_events([
        ev(
            "https://developer.dell.com/inaas-gateway/hipSystemsAuthService-svc/api/domain-systems/domains/33333333-3333-3333-3333-333333333333/deployment-groups",
            [{"id": 243, "name": "dce-shared-receiver", "type": "Receiver", "description": "dce-shared-receiver"}],
        )
    ])
    assert out["deployment_group"][0].object_id == "243"
    assert out["deployment_group"][0].name == "dce-shared-receiver"
    assert out["deployment_group"][0].parent_domain_id == "33333333-3333-3333-3333-333333333333"
    assert out["deployment_group"][0].extra["deployment_group_type"] == "Receiver"


def test_required_api_id_catalog_maps_partner_system_domain_deployment_group_candidates(tmp_path):
    flow = PortalInventoryFlow(AppConfig(), HipMemory(tmp_path / "mem"))
    account = InventoryEntity("account", "acc-1", "Gmail Account", "accounts", "account")
    partner = InventoryEntity("partner", "partner-1", "AS2TEST", "partners", "partner", parent_account_id="acc-1", parent_account_name="Gmail Account")
    domain = InventoryEntity("domain", "domain-1", "Customer Experience (CX)", "domains", "domain")
    system = InventoryEntity("system", "110", "AIC - DCE", "systems", "system", parent_domain_id="domain-1", parent_domain_name="Customer Experience (CX)")
    dg = InventoryEntity("deployment_group", "243", "dce-shared-receiver", "deployment-groups", "deployment_group", parent_domain_id="domain-1", parent_domain_name="Customer Experience (CX)", extra={"details": {"type": "Receiver"}})
    catalog = flow._build_required_api_id_catalog({"account": [account], "partner": [partner], "domain": [domain], "system": [system], "deployment_group": [dg]})
    assert catalog["available_from_partner_system_inventory"]["accountId_or_x_account_id_candidates"][0]["id"] == "acc-1"
    assert catalog["available_from_partner_system_inventory"]["partner_id_candidates"][0]["id"] == "partner-1"
    assert catalog["available_from_partner_system_inventory"]["domainId_candidates"][0]["id"] == "domain-1"
    assert catalog["available_from_partner_system_inventory"]["target_system_id_candidates"][0]["id"] == "110"
    assert catalog["available_from_partner_system_inventory"]["target_deployment_group_id_candidates_receiver_type"][0]["id"] == "243"
    assert "source_document_type_id" in catalog["not_available_from_partner_system_inventory"]
