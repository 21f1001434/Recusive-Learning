from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hip_id_agent.config import AppConfig
from hip_id_agent.form_api_agent import (
    FormAPIAgentQRuntime,
    build_api_contracts,
    build_input_api_crosswalk,
    build_openapi_document,
    build_postman_collection,
    classify_endpoint,
    endpoint_template,
    phase_payload,
    type_shape,
)
from hip_id_agent.models import NetworkTabEvent


def _input() -> dict:
    return json.loads(Path("examples/uhaul_poasn_full_dummy_input.json").read_text(encoding="utf-8"))


def _rule_graph() -> dict:
    return {
        "nodes": [
            {
                "node_id": "rule.name",
                "field_key": "rule_name",
                "input_path": "$.objects.rule.name",
                "section": "Rule",
                "action": "fill_text",
                "semantic_locator": {"names": ["ruleName"]},
            },
            {
                "node_id": "rule.action.target",
                "field_key": "mapping_identifier_name_version",
                "input_path": "$.objects.rule.actions.mapping_identifier_name_version",
                "section": "Actions",
                "action": "select_single",
                "semantic_locator": {"names": ["target"]},
            },
        ]
    }


def test_endpoint_template_and_classifier_are_observation_grounded():
    url = "https://developer.dell.com/api/rules/123e4567-e89b-12d3-a456-426614174000?x=1"
    assert endpoint_template(url).endswith("/api/rules/{uuid}")
    assert classify_endpoint("GET", url) == "read"
    assert classify_endpoint("POST", "https://x/rules/validate", {"name": "x"}) == "validation"
    assert classify_endpoint("POST", "https://x/rules/create", {"name": "x"}) == "create"
    assert classify_endpoint("PATCH", "https://x/rules/10", {"name": "x"}) == "update"


def test_api_contracts_capture_request_response_shapes_and_redacted_examples():
    events = [
        NetworkTabEvent(
            request_id="1",
            url="https://developer.dell.com/api/rules/create",
            method="POST",
            status=201,
            request_body_redacted={"name": "***MASKED***", "conditions": [{"operator": "Equals"}]},
            response_body_redacted={"id": 42, "status": "created"},
            stage="fill_target_branch",
        ),
        NetworkTabEvent(
            request_id="2",
            url="https://developer.dell.com/api/rules/create",
            method="POST",
            status=409,
            request_body_redacted={"name": "***MASKED***", "conditions": []},
            response_body_redacted={"error": "exists"},
            stage="fill_target_branch",
        ),
    ]
    contracts = build_api_contracts(events)
    assert len(contracts) == 1
    row = contracts[0]
    assert row["endpoint_kind"] == "create"
    assert row["mutation_capable"] is True
    assert row["statuses"] == [201, 409]
    assert row["request_shapes"]
    assert row["response_shapes"]
    assert row["request_examples_redacted"][0]["name"] == "***MASKED***"


def test_crosswalk_maps_input_paths_without_persisting_customer_values():
    payload = _input()
    contracts = build_api_contracts([
        NetworkTabEvent(
            request_id="1", url="https://x/rule/create", method="POST",
            request_body_redacted={"ruleName": "***MASKED***", "target": "***MASKED***"},
        )
    ])
    result = build_input_api_crosswalk(
        phase="rule", input_payload=payload, state_graph=_rule_graph(), observed_contracts=contracts
    )
    text = json.dumps(result)
    assert result["mapped_node_count"] == 2
    assert result["values_persisted"] is False
    assert "DELLCoXMLASNXX08C_U-HAUL_RULE" not in text
    assert any(row["api_key_match"] for row in result["rows"])


def test_phase_payload_selects_exact_current_object():
    payload = _input()
    assert phase_payload(payload, "rule")["name"] == payload["objects"]["rule"]["name"]
    assert phase_payload(payload, "biz_flow")["flow_details"]["current_flow_version"] == "1"


def test_openapi_and_postman_are_generated_only_from_observed_contracts():
    contracts = build_api_contracts([
        NetworkTabEvent(request_id="1", url="https://x/api/maps/list?page=1", method="GET", status=200),
        NetworkTabEvent(request_id="2", url="https://x/api/maps/validate", method="POST", status=200, request_body_redacted={"name": "x"}),
    ])
    openapi = build_openapi_document(contracts, title="Observed")
    postman = build_postman_collection(contracts, name="Observed")
    assert openapi["x-generation-policy"].startswith("observed traffic only")
    assert "/api/maps/list" in openapi["paths"]
    assert len(postman["item"]) == 2
    assert postman["variable"][0]["key"] == "HIP_API_TOKEN"


def test_api_fusion_config_is_fail_closed_for_write_by_default():
    cfg = AppConfig()
    assert cfg.api.capture_observed_contracts is True
    assert cfg.api.dual_ui_api is False
    assert cfg.api.capture_submit_payload is False
    assert cfg.api.execution_mode == "capture"
    assert cfg.api.allow_mutating_methods is False
    assert cfg.api.mutation_confirmation_env_var == "HIP_ALLOW_API_MUTATION"


class _FakeRoute:
    def __init__(self):
        self.aborted = False
        self.continued = False

    async def abort(self, reason: str):
        self.aborted = True
        self.reason = reason

    async def continue_(self):
        self.continued = True


class _FakeRequest:
    method = "POST"
    url = "https://developer.dell.com/api/rules/create"
    post_data_json = {"name": "RULE", "conditions": [{"operator": "Equals"}]}
    post_data = json.dumps(post_data_json)

    async def all_headers(self):
        return {"content-type": "application/json", "authorization": "Bearer secret"}


class _FakeLocator:
    def __init__(self, page):
        self.page = page

    async def click(self, timeout: int = 0):
        route = _FakeRoute()
        await self.page.handler(route, _FakeRequest())
        self.page.last_route = route


class _FakePage:
    url = "https://developer.dell.com/hybrid-integrations/bizlink/rules"

    def __init__(self):
        self.handler = None
        self.last_route = None

    async def evaluate(self, script, arg=None):
        if "data-hip-api-capture-submit" in script and "rank" in script:
            return {"found": True, "text": "Create", "score": 100, "tag": "button"}
        return None

    async def route(self, pattern, handler):
        self.handler = handler

    async def unroute(self, pattern, handler=None):
        self.handler = None

    def locator(self, selector):
        return _FakeLocator(self)

    async def wait_for_timeout(self, ms):
        return None


@pytest.mark.asyncio
async def test_submit_capture_aborts_mutation_and_exports_exact_payload(tmp_path):
    cfg = AppConfig()
    cfg.api.capture_submit_payload = True
    browser = SimpleNamespace(page=_FakePage(), network_tab_events=[], network_records=[], action_events=[])
    runtime = FormAPIAgentQRuntime(config=cfg, root_dir=tmp_path, browser=browser)
    result = await runtime._capture_submit_request(phase="rule", out_dir=tmp_path)
    assert result["status"] == "captured"
    assert result["requests"][0]["method"] == "POST"
    assert result["requests"][0]["blocked_before_backend"] is True
    assert browser.page.last_route.aborted is True
    persisted = json.loads((tmp_path / "blocked_submit_api_capture.json").read_text(encoding="utf-8"))
    assert persisted["backend_mutation_possible"] is False
    assert "secret" not in json.dumps(persisted).lower()


@pytest.mark.asyncio
async def test_finish_attempt_writes_complete_ui_api_bundle_and_agentq_ranking(tmp_path):
    cfg = AppConfig()
    cfg.api.dual_ui_api = False
    cfg.api.require_capture_for_completion = True
    browser = SimpleNamespace(
        page=None,
        context=None,
        action_events=[],
        network_records=[],
        network_tab_events=[],
    )
    runtime = FormAPIAgentQRuntime(config=cfg, root_dir=tmp_path, browser=browser)
    runtime.begin_attempt(phase="rule", attempt=1, phase_dir=tmp_path / "rule", input_payload=_input(), state_graph=_rule_graph())
    browser.network_tab_events.extend([
        NetworkTabEvent(request_id="1", url="https://developer.dell.com/api/rules/options", method="GET", status=200, stage="capture_add_form"),
        NetworkTabEvent(request_id="2", url="https://developer.dell.com/api/rules/validate", method="POST", status=200, request_body_redacted={"ruleName": "***MASKED***"}, stage="fill_target_branch"),
    ])
    result = await runtime.finish_attempt(
        phase="rule", attempt=1, success=True,
        verification={"status": "pass"}, judge_result={"pass": True},
    )
    assert result["pass"] is True
    assert result["observed_api_contract_count"] == 2
    out = tmp_path / "rule" / "form_api_intelligence" / "attempt_01"
    for name in (
        "form_open_api_contracts.json", "ui_fill_api_contracts.json", "phase_api_catalog.json",
        "observed_openapi.json", "postman_collection.json", "ui_api_input_crosswalk.json",
        "agentq_api_action_ranking.json", "form_api_agentq_result.json",
    ):
        assert (out / name).is_file(), name
    assert result["agentq_ranked_contracts"]


@pytest.mark.asyncio
async def test_write_mode_requires_both_config_and_environment_confirmation(tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.api.dual_ui_api = True
    cfg.api.execution_mode = "write"
    cfg.api.allow_mutating_methods = True
    browser = SimpleNamespace(page=None, context=None, network_tab_events=[], network_records=[], action_events=[])
    runtime = FormAPIAgentQRuntime(config=cfg, root_dir=tmp_path, browser=browser)
    monkeypatch.delenv("HIP_ALLOW_API_MUTATION", raising=False)
    result = await runtime._execute_api_plan(
        submit_capture={"requests": [{"url": "https://x/create", "method": "POST", "body_redacted": {"name": "x"}}]},
        contracts=[], out_dir=tmp_path,
    )
    assert result["status"] == "blocked_missing_explicit_mutation_confirmation"
    assert result["mutation_sent"] is False


def test_type_shape_is_value_free_and_handles_mixed_lists():
    shape = type_shape({"rows": [{"a": 1}, {"a": "x"}], "enabled": True})
    assert shape["enabled"] == "boolean"
    assert isinstance(shape["rows"], list)
    assert "x" not in json.dumps(shape)


def test_cli_contains_agentq_crawler_and_dual_api_profiles():
    text = Path("hip_id_agent/cli.py").read_text(encoding="utf-8")
    assert "--agentq-crawler-fusion" in text
    assert "--dual-ui-api" in text
    assert "--capture-submit-api" in text
    assert "HIP_ALLOW_API_MUTATION=YES" in text

class _FakeAPIResponse:
    status = 201
    ok = True

    async def text(self):
        return json.dumps({"id": 42, "status": "created"})


class _FakeAPIRequestContext:
    def __init__(self):
        self.calls = []

    async def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeAPIResponse()


@pytest.mark.asyncio
async def test_confirmed_write_replays_ephemeral_exact_request_without_persisting_secret(tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.api.dual_ui_api = True
    cfg.api.execution_mode = "write"
    cfg.api.allow_mutating_methods = True
    api = _FakeAPIRequestContext()
    browser = SimpleNamespace(
        page=None,
        context=SimpleNamespace(request=api),
        network_tab_events=[], network_records=[], action_events=[],
    )
    runtime = FormAPIAgentQRuntime(config=cfg, root_dir=tmp_path, browser=browser)
    monkeypatch.setenv("HIP_ALLOW_API_MUTATION", "YES")
    result = await runtime._execute_api_plan(
        submit_capture={
            "requests": [{
                "url": "https://x/create", "method": "POST",
                "headers_redacted": {"authorization": "***MASKED***"},
                "body_redacted": {"name": "***MASKED***"},
            }],
            "_ephemeral_requests": [{
                "url": "https://x/create", "method": "POST",
                "headers": {"content-type": "application/json", "authorization": "Bearer live-secret", "cookie": "session=private"},
                "body": {"name": "EXACT_RULE", "conditions": [{"operator": "Equals"}]},
            }],
        },
        contracts=[], out_dir=tmp_path,
    )
    assert result["status"] == "executed"
    assert api.calls[0][1]["data"]["name"] == "EXACT_RULE"
    assert api.calls[0][1]["headers"]["authorization"] == "Bearer live-secret"
    assert "cookie" not in {k.lower() for k in api.calls[0][1]["headers"]}
    persisted = (tmp_path / "api_execution_result.json").read_text(encoding="utf-8")
    assert "EXACT_RULE" not in persisted
    assert "live-secret" not in persisted

@pytest.mark.asyncio
async def test_validate_mode_uses_observed_ui_validation_evidence_without_sending_masked_body(tmp_path):
    cfg = AppConfig()
    cfg.api.dual_ui_api = True
    cfg.api.execution_mode = "validate"
    api = _FakeAPIRequestContext()
    browser = SimpleNamespace(page=None, context=SimpleNamespace(request=api), network_tab_events=[], network_records=[], action_events=[])
    runtime = FormAPIAgentQRuntime(config=cfg, root_dir=tmp_path, browser=browser)
    contracts = [{
        "endpoint_kind": "validation", "method": "POST",
        "observed_urls": ["https://x/rules/validate"],
        "endpoint_template": "https://x/rules/validate",
        "request_examples_redacted": [{"name": "***MASKED***"}],
        "statuses": [200], "response_shapes": [{"valid": "boolean"}],
    }]
    result = await runtime._execute_api_plan(submit_capture={}, contracts=contracts, out_dir=tmp_path)
    assert result["status"] == "validated_from_observed_ui_traffic"
    assert result["mutation_sent"] is False
    assert api.calls == []
